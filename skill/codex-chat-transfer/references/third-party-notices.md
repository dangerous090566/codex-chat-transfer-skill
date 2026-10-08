# Third-party notices

## codex-claude-transfer (`cct`)

- Project: `ahmojo/codex-claude-transfer`
- Source: https://github.com/ahmojo/codex-claude-transfer
- Version: 2.0.0 + local `large512.1` patch
- License: MIT
- Bundled file: `assets/cct.exe`
- Bundled executable SHA-256: `94ECEC6D2A82184319500795BB600C3D57D212814E512C64480078CC2403BD20`
- Upstream Windows archive: `cct_v2.0.0_windows_amd64.tar.gz`
- Upstream archive SHA-256: `967EE3379BECB50264DE665E7CD4AB0C4BCAB52D42C45F21FD50BA829F4B8DBC`
- Release: https://github.com/ahmojo/codex-claude-transfer/releases/tag/v2.0.0

The bundled executable is unsigned and locally built from the upstream v2.0.0 source tag with Go 1.27.1 for windows/amd64, CGO disabled, `-trimpath`, and stripped debug symbols. The source ZIP SHA-256 is `577e668b780d35c5aa2f9fdba084ba5ccacac0ac1486ec06f0bc6e089c2ac0e4`.

The only behavior patch is in `internal/bundle/limits.go`: `MaxSessionBytes = 100 << 20` becomes `MaxSessionBytes = 512 << 20`. The 2 GiB total bundle limit, 16 MiB metadata limit, entry-count limit, checksums, path guards, and secret detection remain unchanged. The version label is `v2.0.0+large512.1`.

To reproduce, run `scripts/build_runtime.py --go <go.exe> --work <build-cache> --output <new-cct.exe>`. It pins and verifies the source ZIP, applies the limit patch, runs the upstream limit/integrity tests, and builds the runtime. Update the wrapper's expected hash and this notice together if intentionally changing the build. The upstream archive hashes above document the original release, not the patched executable.

### MIT License

Copyright (c) 2026 cct contributors

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
