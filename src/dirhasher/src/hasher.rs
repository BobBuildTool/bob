use sha1::{Digest, Sha1};
use std::ffi::{OsStr, OsString};
use std::fs::{self, File, FileType, Metadata};
use std::io::{Error, ErrorKind, Read, Write};
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{FileTypeExt, MetadataExt};
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::time::{Duration, Instant};
use tempfile::NamedTempFile;
use threadpool;

pub type Sha1Sum = [u8; 20];

pub enum HashSum {
    Empty,
    Sha1(Sha1Sum),
    RDev([u8; 8]),
}

type Inode = usize;

/// A warning in the style of the Python logging module: a format string with `%s` placeholders and
/// the matching arguments.
pub struct Warning {
    pub msg: &'static str,
    pub args: Vec<String>,
}

impl Warning {
    /// Warning about a failed I/O operation on `path`.
    fn io(msg: &'static str, path: &Path, err: &Error) -> Self {
        Warning {
            msg,
            args: vec![format!("{}: {}", path.display(), err)],
        }
    }

    fn stat(path: &Path, err: &Error) -> Self {
        Warning {
            msg: "Cannot stat '%s': %s",
            args: vec![path.display().to_string(), err.to_string()],
        }
    }
}

/// Signals that the hashing was aborted by the host.
pub struct Aborted;

/// Interface to the caller of the hasher. All methods are invoked on the calling thread.
pub trait Host {
    /// Report a warning. An error aborts the hashing.
    fn warn(&mut self, warning: Warning) -> Result<(), Aborted>;

    /// Invoked roughly every `POLL_INTERVAL` while hashing. An error aborts the hashing.
    fn poll(&mut self) -> Result<(), Aborted>;
}

const POLL_INTERVAL: Duration = Duration::from_millis(100);

/// Messages from the worker threads to the calling thread.
enum Msg {
    ReadDir(ReadDirResult),
    Hashed(Inode, Sha1Sum),
    Warning(Warning),
}

/// Compact subset of `Metadata` with just the fields that are needed.
struct Stat {
    file_type: FileType,
    mode: u32,
    size: u64,
    ctime: i64,
    mtime: i64,
    dev: u64,
    ino: u64,
    rdev: u64,
}

impl From<&Metadata> for Stat {
    fn from(md: &Metadata) -> Self {
        Stat {
            file_type: md.file_type(),
            mode: md.mode(),
            size: md.size(),
            ctime: md.ctime() * 1_000_000_000 + md.ctime_nsec(),
            mtime: md.mtime() * 1_000_000_000 + md.mtime_nsec(),
            dev: md.dev(),
            ino: md.ino(),
            rdev: md.rdev(),
        }
    }
}

struct DirEntry {
    name: OsString,
    ino: Inode,
}

type DirEntries = Vec<DirEntry>;

struct VNode {
    stat: Stat,
    children: DirEntries,
    hash: Option<HashSum>,
}

impl VNode {
    fn new(stat: Stat) -> Self {
        VNode {
            stat,
            children: Vec::new(),
            hash: None,
        }
    }
}

/// Determine the current time of the file system clock.
///
/// File time stamps have a limited granularity. A file that is modified again in the same clock
/// tick after it was hashed would keep its stat data. Such "racily clean" entries must not be
/// trusted. Any file whose ctime is not older than the current file system time is thus not
/// recorded in the index. The ctime is used because it cannot be set by the user. Creating a file
/// gets us the current time in the right granularity, even on network file systems where the clock
/// of the server is used.
fn calc_ctime_limit(index_path: &Path) -> i64 {
    // Use the directory of the index file. A bare file name has an empty parent, which means the
    // current directory. If there is no parent at all, use the global temporary directory as
    // fallback.
    let dir = match index_path.parent() {
        Some(p) if p.is_empty() => PathBuf::from("."),
        Some(p) => p.to_path_buf(),
        None => std::env::temp_dir(),
    };
    let Ok(tmp_file) = tempfile::tempfile_in(dir) else {
        return i64::MIN;
    };

    let Ok(metadata) = tmp_file.metadata() else {
        return i64::MIN;
    };

    metadata.ctime() * 1_000_000_000 + metadata.ctime_nsec()
}

/// Hash a file in a worker thread. Stops early if the hashing was `cancelled`.
fn hash_file(path: &Path, cancelled: &AtomicBool, tx: &mpsc::Sender<Msg>) -> Sha1Sum {
    let mut hasher = Sha1::new();

    match File::open(path) {
        Ok(mut file) => {
            let mut buffer = [0u8; 128 * 1024];
            while !cancelled.load(Ordering::Relaxed) {
                match file.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(len) => hasher.update(&buffer[..len]),
                    Err(err) => {
                        if err.kind() != ErrorKind::Interrupted {
                            let warning = Warning::io("Cannot hash file: %s", path, &err);
                            let _ = tx.send(Msg::Warning(warning));
                            break;
                        }
                    }
                }
            }
        }
        Err(err) => {
            let _ = tx.send(Msg::Warning(Warning::io("Cannot hash file: %s", path, &err)));
        }
    }

    return hasher.finalize().into();
}

fn hash_link(path: &Path, host: &mut dyn Host) -> Result<Sha1Sum, Aborted> {
    let mut hasher = Sha1::new();
    match path.read_link() {
        Ok(destination) => hasher.update(&destination.into_os_string().as_bytes()),
        Err(err) => host.warn(Warning::io("Cannot hash link: %s", path, &err))?,
    }
    Ok(hasher.finalize().into())
}

trait DirHasherIndex {
    fn check(&mut self, path: &Path, stat: &Stat) -> Option<Sha1Sum>;
    fn update(&mut self, path: &Path, stat: &Stat, hash: &Sha1Sum);
    /// Write back the updated index.
    fn finish(&self) -> Result<(), Warning>;
}

struct NullIndex {}

impl DirHasherIndex for NullIndex {
    fn check(&mut self, _path: &Path, _stat: &Stat) -> Option<Sha1Sum> {
        None
    }
    fn update(&mut self, _path: &Path, _stat: &Stat, _hash: &Sha1Sum) {}
    fn finish(&self) -> Result<(), Warning> {
        Ok(())
    }
}

struct FileIndexEntry<'a> {
    data: &'a [u8],
}

const INDEX_SIGNATURE: &[u8; 4] = b"BOB2";
const INDEX_ENTRY_SIZE: usize = 66;
const INDEX_OFF_CTIME: usize = 0;
const INDEX_OFF_MTIME: usize = 8;
const INDEX_OFF_DEV: usize = 16;
const INDEX_OFF_INO: usize = 24;
const INDEX_OFF_MODE: usize = 32;
const INDEX_OFF_SIZE: usize = 36;
const INDEX_OFF_DIGEST: usize = 44;
const INDEX_OFF_NAMELEN: usize = 64;

impl FileIndexEntry<'_> {
    fn new(data: &[u8]) -> Option<FileIndexEntry<'_>> {
        if data.len() < INDEX_ENTRY_SIZE {
            return None;
        }
        let name_len_bytes: [u8; 2] = data[INDEX_OFF_NAMELEN..][0..2].try_into().unwrap();
        let name_len: usize = u16::from_ne_bytes(name_len_bytes).into();
        let len = INDEX_ENTRY_SIZE + name_len;
        if data.len() < len {
            return None;
        }
        Some(FileIndexEntry { data: &data[..len] })
    }

    fn get_name(&self) -> &OsStr {
        OsStr::from_bytes(&self.data[INDEX_ENTRY_SIZE..])
    }

    fn len(&self) -> usize {
        self.data.len()
    }

    fn cmp_stat(&self, stat: &Stat) -> bool {
        let ctime = i64::from_ne_bytes(self.data[INDEX_OFF_CTIME..][0..8].try_into().unwrap());
        let mtime = i64::from_ne_bytes(self.data[INDEX_OFF_MTIME..][0..8].try_into().unwrap());
        let dev = u64::from_ne_bytes(self.data[INDEX_OFF_DEV..][0..8].try_into().unwrap());
        let ino = u64::from_ne_bytes(self.data[INDEX_OFF_INO..][0..8].try_into().unwrap());
        let mode = u32::from_ne_bytes(self.data[INDEX_OFF_MODE..][0..4].try_into().unwrap());
        let size = u64::from_ne_bytes(self.data[INDEX_OFF_SIZE..][0..8].try_into().unwrap());

        return stat.ctime == ctime
            && stat.mtime == mtime
            && stat.dev == dev
            && stat.ino == ino
            && stat.mode == mode
            && stat.size == size;
    }

    fn hash_sum(&self) -> Sha1Sum {
        self.data[INDEX_OFF_DIGEST..][0..20].try_into().unwrap()
    }
}

struct FileIndex {
    index_path: PathBuf,
    ctime_limit: i64,
    old_index: Vec<u8>,
    new_index: Vec<u8>,
    old_pos: usize,
    mismatch: bool,
}

impl FileIndex {
    fn new(index_path: &Path) -> (FileIndex, Option<Warning>) {
        let mut warning = None;
        let index = match fs::read(index_path) {
            Ok(data) => data,
            Err(err) => {
                if err.kind() != ErrorKind::NotFound {
                    warning = Some(Warning::io("Cannot open hash cache: %s", index_path, &err));
                };
                Vec::new()
            }
        };

        let mismatch = index.len() < INDEX_SIGNATURE.len() || index[0..4] != *INDEX_SIGNATURE;
        let mut new_index: Vec<u8> = Vec::with_capacity(index.len());
        new_index.extend_from_slice(INDEX_SIGNATURE);

        let index = FileIndex {
            index_path: index_path.to_path_buf(),
            ctime_limit: calc_ctime_limit(index_path),
            old_index: index,
            new_index: new_index,
            old_pos: if mismatch { 0 } else { 4 },
            mismatch: mismatch,
        };
        (index, warning)
    }

    fn write_back(&self) -> std::io::Result<()> {
        if !self.mismatch {
            return Ok(());
        }
        let mut file = NamedTempFile::new_in(
            self.index_path
                .parent()
                .ok_or(Error::new(ErrorKind::NotADirectory, "No parent directory!"))?,
        )?;
        let _ = file.write_all(&self.new_index)?;
        file.persist(&self.index_path)?;
        Ok(())
    }

    fn search_entry(&mut self, name: &OsStr) -> Option<FileIndexEntry<'_>> {
        loop {
            let entry = FileIndexEntry::new(&self.old_index[self.old_pos..])?;
            let entry_name = entry.get_name();
            if entry_name == name {
                self.old_pos += entry.len();
                return Some(entry);
            } else if entry_name < name {
                self.old_pos += entry.len();
                self.mismatch = true;
            } else {
                self.mismatch = true;
                return None;
            }
        }
    }

    fn add_entry(&mut self, name: &OsStr, stat: &Stat, hash_sum: &Sha1Sum) {
        // Entries that are of the same age than the current file system time must be dropped.
        // Changes in the same "file system tick" cannot be distinguished. Entries that are newer
        // have been recently modified. Their checksum is most probably wrong.
        if stat.ctime >= self.ctime_limit {
            return;
        }

        self.new_index.extend_from_slice(&stat.ctime.to_ne_bytes());
        self.new_index.extend_from_slice(&stat.mtime.to_ne_bytes());
        self.new_index.extend_from_slice(&stat.dev.to_ne_bytes());
        self.new_index.extend_from_slice(&stat.ino.to_ne_bytes());
        self.new_index.extend_from_slice(&stat.mode.to_ne_bytes());
        self.new_index.extend_from_slice(&stat.size.to_ne_bytes());
        self.new_index.extend_from_slice(hash_sum);

        let name_bytes = name.as_bytes();
        let name_len: u16 = name_bytes.len().try_into().unwrap();
        self.new_index.extend_from_slice(&name_len.to_ne_bytes());
        self.new_index.extend_from_slice(&name_bytes);
    }
}

impl DirHasherIndex for FileIndex {
    fn check(&mut self, path: &Path, stat: &Stat) -> Option<Sha1Sum> {
        let entry_path = path.as_os_str();
        if let Some(cached_entry) = self.search_entry(entry_path) {
            if cached_entry.cmp_stat(stat) {
                return Some(cached_entry.hash_sum());
            }
        }

        // Either there was no cache entry or it did not match.
        self.mismatch = true;
        None
    }

    fn update(&mut self, path: &Path, stat: &Stat, hash: &Sha1Sum) {
        if self.mismatch {
            self.add_entry(path.as_os_str(), stat, hash);
        }
    }

    fn finish(&self) -> Result<(), Warning> {
        self.write_back()
            .map_err(|err| Warning::io("Cannot write hash cache: %s", &self.index_path, &err))
    }
}

/// Reusable path buffer for the tree traversal. Holds the prefix, a separator
/// and the relative path. Children are appended and truncated again.
struct PathBuilder {
    buf: Vec<u8>,
    prefix_len: usize,
    base: usize,
}

impl PathBuilder {
    fn new(prefix: &Path) -> Self {
        let mut buf = prefix.as_os_str().as_bytes().to_vec();
        let prefix_len = buf.len();
        if !buf.is_empty() && !buf.ends_with(b"/") {
            buf.push(b'/');
        }
        let base = buf.len();
        PathBuilder {
            buf,
            prefix_len,
            base,
        }
    }

    /// Path relative to the prefix.
    fn rel(&self) -> &Path {
        Path::new(OsStr::from_bytes(&self.buf[self.base..]))
    }

    /// Full path including the prefix.
    fn full(&self) -> &Path {
        let end = if self.buf.len() == self.base {
            self.prefix_len
        } else {
            self.buf.len()
        };
        Path::new(OsStr::from_bytes(&self.buf[..end]))
    }

    fn push(&mut self, name: &OsStr) -> usize {
        let len = self.buf.len();
        self.buf.extend_from_slice(name.as_bytes());
        len
    }

    fn truncate(&mut self, len: usize) {
        self.buf.truncate(len);
    }
}

struct ReadDirEntry {
    name: OsString,
    stat: Stat,
}

struct ReadDirResult {
    path: PathBuf,
    ino: Inode,
    entries: Vec<ReadDirEntry>,
}

struct DirHasher<'a> {
    host: &'a mut dyn Host,
    index: Box<dyn DirHasherIndex>,
    size: u64,
    ignore_dirs: Vec<OsString>,
    ignore_files: Vec<OsString>,
    vnodes: Vec<VNode>,
    pool: threadpool::ThreadPool,
    /// Number of worker jobs whose result is still outstanding.
    pending: u32,
    tx: mpsc::Sender<Msg>,
    rx: mpsc::Receiver<Msg>,
    cancelled: Arc<AtomicBool>,
    next_poll: Instant,
}

impl<'a> DirHasher<'a> {
    fn new(
        host: &'a mut dyn Host,
        index_path: Option<&Path>,
        add_ignore_dirs: Option<Vec<OsString>>,
    ) -> Result<Self, Aborted> {
        let mut ignore_dirs: Vec<OsString> =
            vec![".git".into(), ".portage-cache".into(), ".svn".into()];
        if let Some(add_ignore_dirs) = add_ignore_dirs {
            ignore_dirs.extend(add_ignore_dirs);
        }
        let index: Box<dyn DirHasherIndex> = match index_path {
            Some(index_path) => {
                let (index, warning) = FileIndex::new(index_path);
                if let Some(warning) = warning {
                    host.warn(warning)?;
                }
                Box::new(index)
            }
            None => Box::new(NullIndex {}),
        };
        let (tx, rx) = mpsc::channel::<Msg>();
        Ok(DirHasher {
            host,
            index,
            size: 0,
            ignore_dirs,
            ignore_files: vec!["BaseDirList.txt".into()],
            vnodes: Vec::new(),
            pool: threadpool::Builder::new().build(),
            pending: 0,
            tx,
            rx,
            cancelled: Arc::new(AtomicBool::new(false)),
            next_poll: Instant::now() + POLL_INTERVAL,
        })
    }

    /// Poll the host if `POLL_INTERVAL` has elapsed since the last time.
    fn maybe_poll(&mut self) -> Result<(), Aborted> {
        let now = Instant::now();
        if now >= self.next_poll {
            self.host.poll()?;
            self.next_poll = now + POLL_INTERVAL;
        }
        Ok(())
    }

    fn handle_msg(&mut self, msg: Msg) -> Result<(), Aborted> {
        match msg {
            Msg::ReadDir(res) => {
                self.pending -= 1;
                self.process_readdir(res);
            }
            Msg::Hashed(ino, hash) => {
                self.pending -= 1;
                self.vnodes[ino].hash = Some(HashSum::Sha1(hash));
            }
            Msg::Warning(warning) => self.host.warn(warning)?,
        }
        Ok(())
    }

    /// Handle the messages of the workers until all jobs are finished.
    fn wait_pending(&mut self) -> Result<(), Aborted> {
        while self.pending > 0 {
            let timeout = self.next_poll.saturating_duration_since(Instant::now());
            match self.rx.recv_timeout(timeout) {
                Ok(msg) => self.handle_msg(msg)?,
                Err(mpsc::RecvTimeoutError::Timeout) => (),
                Err(mpsc::RecvTimeoutError::Disconnected) => unreachable!("sender is owned by self"),
            }
            self.maybe_poll()?;
        }
        Ok(())
    }

    fn schedule_readdir(&mut self, path: PathBuf, ino: Inode) {
        let tx = self.tx.clone();
        let cancelled = self.cancelled.clone();
        self.pending += 1;
        self.pool.execute(move || {
            if cancelled.load(Ordering::Relaxed) {
                return;
            }
            let mut entries = Vec::new();
            match fs::read_dir(&path) {
                Ok(iter) => {
                    for entry in iter {
                        let entry = match entry {
                            Ok(entry) => entry,
                            Err(err) => {
                                let warning = Warning::io("Cannot list directory: %s", &path, &err);
                                let _ = tx.send(Msg::Warning(warning));
                                continue;
                            }
                        };
                        let metadata = match entry.metadata() {
                            Ok(metadata) => metadata,
                            Err(err) => {
                                let _ = tx.send(Msg::Warning(Warning::stat(&entry.path(), &err)));
                                continue;
                            }
                        };
                        let name = entry.file_name();
                        entries.push(ReadDirEntry {
                            name,
                            stat: Stat::from(&metadata),
                        });
                    }
                }
                Err(err) => {
                    let warning = Warning::io("Cannot list directory: %s", &path, &err);
                    let _ = tx.send(Msg::Warning(warning));
                }
            };
            // The receiver is gone if the hashing was aborted.
            let _ = tx.send(Msg::ReadDir(ReadDirResult { path, ino, entries }));
        });
    }

    fn process_readdir(&mut self, res: ReadDirResult) {
        let mut dir: DirEntries = Vec::with_capacity(res.entries.len());

        for entry in res.entries {
            let mut name = entry.name;
            if entry.stat.file_type.is_dir() {
                if self.ignore_dirs.contains(&name) {
                    continue;
                }
                name.push("/");
            } else if self.ignore_files.contains(&name) {
                continue;
            }

            let ino: Inode = self.vnodes.len();
            if entry.stat.file_type.is_dir() {
                self.schedule_readdir(res.path.join(&name), ino);
            };
            self.vnodes.push(VNode::new(entry.stat));
            dir.push(DirEntry { name, ino });
        }

        dir.sort_unstable_by(|e1, e2| e1.name.cmp(&e2.name));
        self.vnodes[res.ino].children = dir;
    }

    /// Discover the file system tree at `path`. The caller decides whether a symlink at `path`
    /// itself is followed by passing the respective `metadata`. Symlinks below `path` are never
    /// followed.
    fn discover(&mut self, path: &Path, metadata: &Metadata) -> Result<(), Aborted> {
        let stat = Stat::from(metadata);
        if stat.file_type.is_dir() {
            let ino: Inode = self.vnodes.len();
            self.vnodes.push(VNode::new(stat));
            self.schedule_readdir(PathBuf::from(path), ino);
            self.wait_pending()?;
        } else {
            self.vnodes.push(VNode::new(stat));
        };

        Ok(())
    }

    /// Start collecting hash sums on the discovered file system tree. If unkown files are
    /// encountered, schedule their hashing in the thread pool.
    fn collect_node(&mut self, path: &mut PathBuilder, ino: Inode) -> Result<(), Aborted> {
        self.size += self.vnodes[ino].stat.size;

        let file_type = self.vnodes[ino].stat.file_type;
        if file_type.is_file() {
            match self.index.check(path.rel(), &self.vnodes[ino].stat) {
                Some(hash) => self.vnodes[ino].hash = Some(HashSum::Sha1(hash)),
                None => {
                    let tx = self.tx.clone();
                    let cancelled = self.cancelled.clone();
                    let path = path.full().to_path_buf();
                    self.pending += 1;
                    self.pool.execute(move || {
                        if cancelled.load(Ordering::Relaxed) {
                            return;
                        }
                        let hash = hash_file(&path, &cancelled, &tx);
                        let _ = tx.send(Msg::Hashed(ino, hash));
                    });
                }
            }
        } else if file_type.is_dir() {
            for i in 0..self.vnodes[ino].children.len() {
                let child_ino = self.vnodes[ino].children[i].ino;
                let len = path.push(&self.vnodes[ino].children[i].name);
                self.collect_node(path, child_ino)?;
                path.truncate(len);
            }
        } else if file_type.is_symlink() {
            let hash = match self.index.check(path.rel(), &self.vnodes[ino].stat) {
                Some(hash) => hash,
                None => hash_link(path.full(), &mut *self.host)?,
            };
            self.vnodes[ino].hash = Some(HashSum::Sha1(hash));
        } else if file_type.is_block_device() || file_type.is_char_device() {
            let hash = self.vnodes[ino].stat.rdev.to_le_bytes();
            self.vnodes[ino].hash = Some(HashSum::RDev(hash));
        } else {
            self.vnodes[ino].hash = Some(HashSum::Empty);
            if !file_type.is_fifo() {
                self.host.warn(Warning {
                    msg: "Unknown file: %s",
                    args: vec![path.full().display().to_string()],
                })?;
            }
        }

        Ok(())
    }

    /// Calculate the final hash sum. All VNodes except directories have a hash sum already.
    fn finalize_node(&mut self, path: &mut PathBuilder, ino: Inode) {
        let file_type = self.vnodes[ino].stat.file_type;
        if file_type.is_dir() {
            let mut hasher = Sha1::new();
            for i in 0..self.vnodes[ino].children.len() {
                let child_ino = self.vnodes[ino].children[i].ino;
                let len = path.push(&self.vnodes[ino].children[i].name);
                self.finalize_node(path, child_ino);
                path.truncate(len);
                hasher.update(&self.vnodes[child_ino].stat.mode.to_le_bytes());
                match self.vnodes[child_ino].hash.as_ref().unwrap() {
                    HashSum::Empty => (),
                    HashSum::Sha1(hash) => hasher.update(hash),
                    HashSum::RDev(hash) => hasher.update(hash),
                };
                hasher.update(&self.vnodes[ino].children[i].name.as_bytes());
            }
            self.vnodes[ino].hash = Some(HashSum::Sha1(hasher.finalize().into()));
        }

        if file_type.is_file() || file_type.is_symlink() {
            if let Some(HashSum::Sha1(hash)) = self.vnodes[ino].hash {
                self.index.update(path.rel(), &self.vnodes[ino].stat, &hash);
            }
        }
    }

    /// Hash the tree at `path` and write back the updated index.
    fn hash_tree(&mut self, path: &Path, metadata: &Metadata) -> Result<HashSum, Aborted> {
        self.discover(path, metadata)?;
        self.collect_node(&mut PathBuilder::new(path), 0)?;
        self.wait_pending()?;
        self.finalize_node(&mut PathBuilder::new(Path::new("")), 0);
        if let Err(warning) = self.index.finish() {
            self.host.warn(warning)?;
        }
        Ok(self.vnodes[0].hash.take().unwrap())
    }

    fn hash_dir(mut self, path: &Path) -> Result<(Vec<u8>, u64), Aborted> {
        // A symlink to a directory is followed, like os.scandir() does in Python.
        let metadata = path.metadata().and_then(|md| {
            if md.is_dir() {
                Ok(md)
            } else {
                Err(Error::from(ErrorKind::NotADirectory))
            }
        });
        let metadata = match metadata {
            Ok(metadata) => metadata,
            Err(err) => {
                self.host.warn(Warning::io("Cannot list directory: %s", path, &err))?;
                return Ok((Sha1::digest(b"").to_vec(), 0));
            }
        };

        let HashSum::Sha1(hash) = self.hash_tree(path, &metadata)? else {
            unreachable!("directories have a SHA1 hash");
        };
        Ok((hash.to_vec(), self.size))
    }

    fn hash_path(mut self, path: &Path) -> Result<Vec<u8>, Aborted> {
        let metadata = match path.symlink_metadata() {
            Ok(metadata) => metadata,
            Err(err) => {
                self.host.warn(Warning::stat(path, &err))?;
                return Ok(Vec::new());
            }
        };

        let hash = match self.hash_tree(path, &metadata)? {
            HashSum::Empty => Vec::new(),
            HashSum::Sha1(hash) => hash.to_vec(),
            HashSum::RDev(hash) => hash.to_vec(),
        };
        Ok(hash)
    }
}

impl Drop for DirHasher<'_> {
    fn drop(&mut self) {
        // Let the outstanding worker jobs finish quickly if the hashing was aborted.
        self.cancelled.store(true, Ordering::Relaxed);
    }
}

pub fn hash_dir(
    path: &Path,
    index: Option<&Path>,
    ignore_dirs: Option<Vec<OsString>>,
    host: &mut dyn Host,
) -> Result<(Vec<u8>, u64), Aborted> {
    DirHasher::new(host, index, ignore_dirs)?.hash_dir(path)
}

pub fn hash_path(
    path: &Path,
    index: Option<&Path>,
    ignore_dirs: Option<Vec<OsString>>,
    host: &mut dyn Host,
) -> Result<Vec<u8>, Aborted> {
    DirHasher::new(host, index, ignore_dirs)?.hash_path(path)
}
