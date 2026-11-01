use std::ffi::{OsStr, OsString};
use std::fs::FileType;

pub fn is_block_device(_: &FileType) -> bool {
    false
}

pub fn is_char_device(_: &FileType) -> bool {
    false
}

pub fn is_fifo(_: &FileType) -> bool {
    false
}

pub fn os_str_to_bytes(s: &OsStr) -> &[u8] {
    s.to_str().expect("must not fail").as_bytes()
}

pub fn os_string_to_bytes(s: &OsString) -> &[u8] {
    os_str_to_bytes(s.as_os_str())
}
