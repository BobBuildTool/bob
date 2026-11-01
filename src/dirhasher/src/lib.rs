use pyo3::prelude::*;

mod hasher;

#[pymodule]
mod _dirhasher {
    use crate::hasher::{Aborted, Host, Warning};
    use pyo3::prelude::*;
    use pyo3::types::{PyBytes, PyTuple};
    use std::ffi::OsString;
    use std::path::PathBuf;

    /// Connects the hasher with Python while the GIL is released. The GIL is re-acquired briefly to
    /// log warnings and to handle signals. The first Python exception aborts the hashing.
    #[derive(Default)]
    struct PyHost {
        err: Option<PyErr>,
    }

    impl PyHost {
        fn catch(&mut self, res: PyResult<()>) -> Result<(), Aborted> {
            res.map_err(|err| {
                self.err = Some(err);
                Aborted
            })
        }

        fn into_result<T>(self, res: Result<T, Aborted>) -> PyResult<T> {
            res.map_err(|Aborted| self.err.expect("exception recorded on abort"))
        }
    }

    impl Host for PyHost {
        /// Forward the warning to the logger of the Python implementation.
        fn warn(&mut self, mut warning: Warning) -> Result<(), Aborted> {
            let res = Python::attach(|py| {
                let logger = py
                    .import("logging")?
                    .call_method1("getLogger", ("bob.utils",))?;
                warning.args.insert(0, warning.msg.to_string());
                logger.call_method1("warning", PyTuple::new(py, warning.args)?)?;
                Ok(())
            });
            self.catch(res)
        }

        /// Run the Python signal handlers, e.g. to raise KeyboardInterrupt on Ctrl-C.
        fn poll(&mut self) -> Result<(), Aborted> {
            let res = Python::attach(|py| py.check_signals());
            self.catch(res)
        }
    }

    #[pyfunction]
    fn hash_directory_with_size<'py>(
        py: Python<'py>,
        path: PathBuf,
        index: Option<PathBuf>,
        ignore_dirs: Option<Vec<PathBuf>>,
    ) -> PyResult<(Bound<'py, PyBytes>, u64)> {
        let ignore_dirs: Option<Vec<OsString>> =
            ignore_dirs.map(|v| v.into_iter().map(PathBuf::into_os_string).collect());
        let mut host = PyHost::default();
        let res =
            py.detach(|| crate::hasher::hash_dir(&path, index.as_deref(), ignore_dirs, &mut host));
        let (hash, size) = host.into_result(res)?;
        Ok((PyBytes::new(py, &hash), size))
    }
}
