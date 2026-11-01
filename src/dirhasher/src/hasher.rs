use sha1::{Digest, Sha1};
use std::ffi::{OsStr, OsString};
use std::fs::{self, File, FileType, Metadata};
use std::io::{Error, ErrorKind, Read, Write};
use std::path::{MAIN_SEPARATOR_STR, Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, mpsc};
use std::time::{Duration, Instant};
use tempfile::NamedTempFile;
use threadpool;

#[cfg(unix)]
mod unix {
    pub mod helpers;
    pub use std::os::unix::fs::MetadataExt;
}

#[cfg(unix)]
use unix::*;

#[cfg(windows)]
mod windows {
    pub mod helpers;
    pub use std::os::windows::fs::MetadataExt;
}

#[cfg(windows)]
use windows::*;

use helpers::*; // import the helpers from the unix/windows submodule

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

#[cfg(windows)]
fn win_time_to_unix_ns(t: u64) -> i64 {
    // epoch offset - number of 100 ns ticks between 1601 and 1970
    ((t as i64) - 116444736000000000i64) * 100
}

#[cfg(windows)]
/// Convert file metadata info Python compatible "st_mode".
///
/// Mimics _Py_attribute_data_to_stat() and win32_xstat_impl() from the CPython implementation.
fn win_mode_from_metadata(md: &Metadata, file_name: &OsStr) -> u32 {
    let mut mode: u32 = if md.is_dir() {
        0o040000 // S_IFDIR
    } else if md.is_symlink() {
        0o120000 // S_IFLNK
    } else {
        0o100000 // S_IFREG
    };

    // FILE_ATTRIBUTE_DIRECTORY - this is set for symlinks on directories too
    if (md.file_attributes() & 0x10) != 0 {
        mode |= 0o111;
    }

    // FILE_ATTRIBUTE_READONLY
    if (md.file_attributes() & 0x1) != 0 {
        mode |= 0o444;
    } else {
        mode |= 0o666;
    }

    // Python makes certain files "executable".
    if !md.is_dir() {
        if let Some((_, ext)) = file_name.to_string_lossy().rsplit_once(".") {
            let ext = ext.to_lowercase();
            if ext == ".exe" || ext == ".bat" || ext == ".cmd" || ext == ".com" {
                mode |= 0o111;
            }
        }
    }

    mode
}

impl Stat {
    #[cfg(unix)]
    fn get_change_time(&self) -> i64 {
        self.ctime
    }

    #[cfg(windows)]
    fn get_change_time(&self) -> i64 {
        self.mtime
    }
}

impl Stat {
    #[cfg(unix)]
    fn from(md: &Metadata, _: &OsStr) -> Self {
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

    #[cfg(windows)]
    fn from(md: &Metadata, path: &OsStr) -> Self {
        Stat {
            file_type: md.file_type(),
            mode: win_mode_from_metadata(md, path),
            size: md.file_size(),
            ctime: win_time_to_unix_ns(md.creation_time()),
            mtime: win_time_to_unix_ns(md.last_write_time()),
            // Fortunately the following information is also 0 in the Python implementation. We're
            // using os.DirEntry.stat() and this does not populate these fields either.
            dev: 0,  // md.volume_serial_number() is unfortunately not a stable API
            ino: 0,  // md.file_index() neither
            rdev: 0, // unused on Windows
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

    Stat::from(&metadata, index_path.as_os_str()).get_change_time()
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
            let _ = tx.send(Msg::Warning(Warning::io(
                "Cannot hash file: %s",
                path,
                &err,
            )));
        }
    }

    return hasher.finalize().into();
}

fn hash_link(path: &Path, host: &mut dyn Host) -> Result<Sha1Sum, Aborted> {
    let mut hasher = Sha1::new();
    match path.read_link() {
        Ok(destination) => hasher.update(os_string_to_bytes(&destination.into_os_string())),
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

    fn get_name(&self) -> &[u8] {
        &self.data[INDEX_ENTRY_SIZE..]
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
        let name = os_str_to_bytes(name);
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

        let name_bytes = os_str_to_bytes(name);
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
    stack: Vec<usize>,
    prefix_len: usize,
    base: usize,
}

impl PathBuilder {
    fn new(prefix: &Path) -> Self {
        let mut buf = prefix.as_os_str().as_encoded_bytes().to_vec();
        let prefix_len = buf.len();
        if !prefix.is_empty() && !prefix.ends_with(MAIN_SEPARATOR_STR) {
            buf.extend_from_slice(OsStr::new(MAIN_SEPARATOR_STR).as_encoded_bytes());
        }
        let base = buf.len();
        PathBuilder {
            buf,
            stack: Vec::new(),
            prefix_len,
            base,
        }
    }

    /// Path relative to the prefix.
    fn rel(&self) -> &Path {
        // SAFETY:
        //  - all bytes are from OsStr::as_encoded_bytes()
        //  - no byte sequences is split
        Path::new(unsafe { OsStr::from_encoded_bytes_unchecked(&self.buf[self.base..]) })
    }

    /// Full path including the prefix.
    fn full(&self) -> &Path {
        let end = if self.buf.len() == self.base {
            self.prefix_len
        } else {
            self.buf.len()
        };
        // SAFETY:
        //  - all bytes are from OsStr::as_encoded_bytes()
        //  - no byte sequences is split
        Path::new(unsafe { OsStr::from_encoded_bytes_unchecked(&self.buf[..end]) })
    }

    /// Add new path segment. Assumes trailing separator for directories.
    fn push(&mut self, name: &OsStr) {
        self.stack.push(self.buf.len());
        self.buf.extend_from_slice(name.as_encoded_bytes());
    }

    /// Pop last path segment
    fn pop(&mut self) {
        let len = self.stack.pop().expect("path stack must not be empty");
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
                Err(mpsc::RecvTimeoutError::Disconnected) => {
                    unreachable!("sender is owned by self")
                }
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
                        let stat = Stat::from(&metadata, name.as_os_str());
                        entries.push(ReadDirEntry { name, stat });
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
                name.push(MAIN_SEPARATOR_STR);
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
        let stat = Stat::from(metadata, path.as_os_str());
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
                path.push(&self.vnodes[ino].children[i].name);
                self.collect_node(path, child_ino)?;
                path.pop();
            }
        } else if file_type.is_symlink() {
            let hash = match self.index.check(path.rel(), &self.vnodes[ino].stat) {
                Some(hash) => hash,
                None => hash_link(path.full(), &mut *self.host)?,
            };
            self.vnodes[ino].hash = Some(HashSum::Sha1(hash));
        } else if is_block_device(&file_type) || is_char_device(&file_type) {
            let hash = self.vnodes[ino].stat.rdev.to_le_bytes();
            self.vnodes[ino].hash = Some(HashSum::RDev(hash));
        } else {
            self.vnodes[ino].hash = Some(HashSum::Empty);
            if !is_fifo(&file_type) {
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
                path.push(&self.vnodes[ino].children[i].name);
                self.finalize_node(path, child_ino);
                path.pop();
                hasher.update(&self.vnodes[child_ino].stat.mode.to_le_bytes());
                match self.vnodes[child_ino].hash.as_ref().unwrap() {
                    HashSum::Empty => (),
                    HashSum::Sha1(hash) => hasher.update(hash),
                    HashSum::RDev(hash) => hasher.update(hash),
                };
                hasher.update(os_string_to_bytes(&self.vnodes[ino].children[i].name));
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
                self.host
                    .warn(Warning::io("Cannot list directory: %s", path, &err))?;
                return Ok((Sha1::digest(b"").to_vec(), 0));
            }
        };

        let HashSum::Sha1(hash) = self.hash_tree(path, &metadata)? else {
            unreachable!("directories have a SHA1 hash");
        };
        Ok((hash.to_vec(), self.size))
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
