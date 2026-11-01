use std::ffi::{OsStr, OsString};
use std::fs::FileType;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::FileTypeExt;

pub fn is_block_device(ft: &FileType) -> bool {
    ft.is_block_device()
}

pub fn is_char_device(ft: &FileType) -> bool {
    ft.is_char_device()
}

pub fn is_fifo(ft: &FileType) -> bool {
    ft.is_fifo()
}

pub fn os_str_to_bytes(s: &OsStr) -> &[u8] {
    s.as_bytes()
}

pub fn os_string_to_bytes(s: &OsString) -> &[u8] {
    os_str_to_bytes(s.as_os_str())
}
