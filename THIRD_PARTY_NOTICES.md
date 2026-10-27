# Third-party notices / 第三方组件说明

This application installs third-party dependencies; it does not claim their authorship. The table reflects the tested Python environment and may include build or support packages. License labels are copied from installed distribution metadata. Original license texts, where packaged by the distribution, are preserved under `third_party/licenses/`. Component-specific terms and notices control their use. The Python runtime, Git, Node.js, Codex CLI, and Claude Code are installed separately and are not bundled here.

本应用安装并调用第三方依赖，不主张其原创作者身份。下表依据已验证环境的发行包元数据生成，可能包括构建或辅助依赖；原发行包随附的许可文本保存在 `third_party/licenses/`。Python、Git、Node.js、Codex CLI 与 Claude Code 均单独安装，未包含在本仓库内。

The coding executor is **PsChina/deepseek-as-subagent** (MIT), pinned to commit `7e54026392f85ccfc796f947faf9ffac3968a49e`. Its original copyright and license notice are preserved in the license directory. Agent Room and codex-review-server were inspected only and are not installed or copied into this application. Their exact inspected revisions are documented in `sources.lock.json`.

The version-controlled `gui/executor_limits.py` adapter extends that executor's explicit output-token validator from its 16,384 ceiling to the application's 128,000 range, in both the MCP host and hardened provider child. The installed dependency and its defaults, process isolation, transaction recovery, cancellation, response-byte limits and tool boundaries are unchanged. Room output settings are passed explicitly; provider-side limits still apply.

The `gui/windows_scripts.py` adapter normalizes permitted PowerShell file writes to UTF-8 with BOM before the upstream atomic writer computes its committed-byte digest. The original transaction, identity checks and recovery journal still cover the exact written bytes. The host acknowledges only independently verified committed records within a currently user-approved contract; uncertain, changed or out-of-scope outcomes remain pending.

The optional Voice Studio uses [edge-tts](https://github.com/rany2/edge-tts) 7.3.1 for online speech synthesis; its packaged license is preserved in [third_party/licenses/edge-tts/LICENSE](third_party/licenses/edge-tts/LICENSE). It can also connect to an independently installed [GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS) service. GPT-SoVITS source checkouts, runtime bundles, models and training data are not distributed in this repository; their upstream terms apply when separately downloaded.

| Distribution | Tested version | License metadata | Original license files |
|---|---|---|---|
| annotated-doc | 0.0.5 | MIT | [texts](third_party/licenses/annotated-doc/) |
| annotated-types | 0.8.0 | MIT | [texts](third_party/licenses/annotated-types/) |
| anyio | 4.14.2 | MIT | [texts](third_party/licenses/anyio/) |
| attrs | 26.1.0 | MIT | [texts](third_party/licenses/attrs/) |
| bottle | 0.13.4 | MIT | [texts](third_party/licenses/bottle/) |
| certifi | 2026.7.22 | MPL-2.0 | [texts](third_party/licenses/certifi/) |
| cffi | 2.1.1 | MIT-0 | [texts](third_party/licenses/cffi/) |
| click | 8.5.0 | BSD-3-Clause | [texts](third_party/licenses/click/) |
| clr_loader | 0.3.1 | MIT | [texts](third_party/licenses/clr_loader/) |
| colorama | 0.4.6 | BSD License | [texts](third_party/licenses/colorama/) |
| cryptography | 50.0.1 | Apache-2.0 OR BSD-3-Clause | [texts](third_party/licenses/cryptography/) |
| deepseek-as-subagent | 0.1.0 | MIT | [texts](third_party/licenses/deepseek-as-subagent/) |
| distro | 1.9.0 | Apache License, Version 2.0 | See upstream distribution |
| google-re2 | 1.1.20251105 | BSD License | [texts](third_party/licenses/google-re2/) |
| h11 | 0.16.0 | MIT | [texts](third_party/licenses/h11/) |
| hatchling | 1.32.0 | MIT | [texts](third_party/licenses/hatchling/) |
| httpcore | 1.0.9 | BSD-3-Clause | [texts](third_party/licenses/httpcore/) |
| httpx | 0.28.1 | BSD-3-Clause | [texts](third_party/licenses/httpx/) |
| httpx-sse | 0.4.3 | MIT | [texts](third_party/licenses/httpx-sse/) |
| idna | 3.19 | BSD-3-Clause | [texts](third_party/licenses/idna/) |
| jiter | 0.16.0 | MIT | [texts](third_party/licenses/jiter/) |
| jsonschema | 4.26.0 | MIT | [texts](third_party/licenses/jsonschema/) |
| jsonschema-specifications | 2025.9.1 | MIT | [texts](third_party/licenses/jsonschema-specifications/) |
| markdown-it-py | 4.2.0 | MIT License | [texts](third_party/licenses/markdown-it-py/) |
| mcp | 1.29.1 | MIT | [texts](third_party/licenses/mcp/) |
| mdurl | 0.1.2 | MIT License | See upstream distribution |
| openai | 2.54.0 | Apache-2.0 | [texts](third_party/licenses/openai/) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [texts](third_party/licenses/packaging/) |
| pathspec | 1.1.1 | Mozilla Public License 2.0 (MPL 2.0) | [texts](third_party/licenses/pathspec/) |
| pluggy | 1.6.0 | MIT | [texts](third_party/licenses/pluggy/) |
| proxy_tools | 0.1.0 | MIT | See upstream distribution |
| pycparser | 3.0 | BSD-3-Clause | [texts](third_party/licenses/pycparser/) |
| pydantic | 2.13.4 | MIT | [texts](third_party/licenses/pydantic/) |
| pydantic-settings | 2.15.0 | MIT | [texts](third_party/licenses/pydantic-settings/) |
| pydantic_core | 2.46.4 | MIT | [texts](third_party/licenses/pydantic_core/) |
| Pygments | 2.21.0 | BSD-2-Clause | [texts](third_party/licenses/Pygments/) |
| PyJWT | 2.15.1 | MIT | [texts](third_party/licenses/PyJWT/) |
| python-dotenv | 1.2.3 | BSD-3-Clause | [texts](third_party/licenses/python-dotenv/) |
| python-multipart | 0.0.32 | Apache-2.0 | [texts](third_party/licenses/python-multipart/) |
| pythonnet | 3.2.1 | MIT | [texts](third_party/licenses/pythonnet/) |
| pywebview | 6.2.1 | BSD-3-Clause | [texts](third_party/licenses/pywebview/) |
| pywin32 | 312 | PSF | [texts](third_party/licenses/pywin32/adodbapi/) |
| PyYAML | 6.0.3 | MIT | [texts](third_party/licenses/PyYAML/) |
| referencing | 0.37.0 | MIT | [texts](third_party/licenses/referencing/) |
| rich | 15.0.0 | MIT | [texts](third_party/licenses/rich/) |
| rpds-py | 2026.6.3 | MIT | [texts](third_party/licenses/rpds-py/) |
| shellingham | 1.5.4 | ISC License | See upstream distribution |
| sniffio | 1.3.1 | MIT OR Apache-2.0 | See upstream distribution |
| socksio | 1.0.0 | UNKNOWN | See upstream distribution |
| sse-starlette | 3.4.8 | BSD-3-Clause | [texts](third_party/licenses/sse-starlette/) |
| starlette | 1.6.0 | BSD-3-Clause | [texts](third_party/licenses/starlette/) |
| tomlkit | 0.15.1 | MIT | [texts](third_party/licenses/tomlkit/) |
| tqdm | 4.70.0 | MPL-2.0 AND MIT | [texts](third_party/licenses/tqdm/) |
| trove-classifiers | 2026.6.1.19 | Apache Software License | [texts](third_party/licenses/trove-classifiers/) |
| typer | 0.27.1 | MIT | [texts](third_party/licenses/typer/) |
| typing-inspection | 0.4.4 | MIT | [texts](third_party/licenses/typing-inspection/) |
| typing_extensions | 4.16.0 | PSF-2.0 | [texts](third_party/licenses/typing_extensions/) |
| tzdata | 2026.5 | Apache-2.0; IANA timezone data public domain | [texts](third_party/licenses/tzdata/) |
| uvicorn | 0.52.4 | BSD-3-Clause | [texts](third_party/licenses/uvicorn/) |

Package source links and exact file paths are listed in [third_party/dependencies.json](third_party/dependencies.json). No license from this table is automatically assigned to the new application code; see [LICENSE_STATUS.md](LICENSE_STATUS.md).
