# Environment

## Node

The perf gate uses the local `node_modules` installed by `npm install`.

Verified commands:

```powershell
npm run typecheck
npm test
python -m py_compile compat\python\planner_engine_client.py compat\python\flask_adapter_example.py compat\python\native_kernel_adapter.py
```

## Rust

Rust is configured locally under this workspace:

- `.cargo`
- `.rustup`
- `.tools\rustup-init.exe`

The helper scripts keep those paths local and do not modify global PATH:

```powershell
.\scripts\install_rust_local.ps1
npm run rust:check
npm run engine:start
```

RustRover is only the IDE layer. It does not replace the Windows MSVC linker,
compiler, or SDK libraries required by the `x86_64-pc-windows-msvc` Rust target.
Run the prerequisite check with:

```powershell
npm run rust:doctor
```

Visual Studio Build Tools 2022 and Windows SDK are now installed on this
machine. `scripts\rust_env.ps1` imports `VsDevCmd.bat` automatically, so the
workspace scripts can find `link.exe`, `cl.exe`, and SDK libraries from a normal
PowerShell session.

Verified commands:

```powershell
npm run rust:doctor
npm run rust:check
```

The failed GNU toolchain attempt left no tracked files; toolchain directories are
ignored by `.gitignore`.
