# Codex Chat Transfer Skill

一个面向 Windows x64 的本地 Codex skill，用于选择性导出多个聊天，并在另一台电脑上安全预检、导入和恢复这些聊天。

## 功能

- 列出并选择多个用户聊天。
- 保留原生线程 ID、会话记录、上下文、工具事件和嵌入内容。
- 保存所选任务的显示名称、本地项目名称、项目归属和工作路径元数据。
- 可携带全局 Markdown 记忆的时间点快照。
- 导入前验证路径、SHA-256、bundle 清单和本机差异。
- 支持显式 `OLD=NEW` 跨设备路径映射，并验证目标项目目录。
- 提供带备份的项目/标题恢复和最终验收；Codex 运行时拒绝写入其索引。
- 相同聊天自动跳过；同 ID 的分叉聊天拒绝覆盖。
- 记忆只增加缺失文件；同名冲突进入隔离目录，不覆盖本机记忆。
- 默认检测并阻止可能包含凭据的导出。

它不会持续同步，不会把整个 `.codex` 目录、`auth.json`、SQLite、WAL/SHM、配置或日志放进迁移包，也不会创建后台监控、启动项或目录链接。完整工作区恢复只在目标机关闭 Codex 后更新必要的索引字段，并会先创建本机备份。

## 安装

```powershell
git clone https://github.com/dangerous090566/codex-chat-transfer-skill.git
Copy-Item -LiteralPath ".\codex-chat-transfer-skill\skill\codex-chat-transfer" -Destination "$env:USERPROFILE\.codex\skills" -Recurse
```

安装后重新启动 Codex。

也可以让 Codex 从本仓库安装 `skill/codex-chat-transfer`。

运行脚本需要 PowerShell 7；Codex 桌面版自带的 `pwsh` 运行时可以直接使用。不要用 Windows PowerShell 5.1 执行工作区恢复。

## 使用

导出与导入默认共用：

```text
%USERPROFILE%\Nutstore\1\我的坚果云\codex文件处理\Codex聊天迁移
```

导出自动创建独立子文件夹；导入先查找这个目录中的迁移包。只有一个已解压包时可以自动选中，有多个时会列出供选择。ZIP 需先完整解压。

可用 `-TransferRoot` 或环境变量 `CODEX_CHAT_TRANSFER_ROOT` 覆盖，也可在本机 `.codex/chat-transfer-settings.json` 中保存 `{"TransferRoot":"绝对路径"}`。机器路径和私有聊天不上传到本仓库。

导出：

> 使用 `$codex-chat-transfer` 列出我的聊天，让我选择若干个后导出。

导入：

> 使用 `$codex-chat-transfer` 检查这个迁移文件夹，确认安全后导入聊天和记忆。

如果两台电脑的用户名或坚果云根目录不同，请同时告诉 Codex 源路径与本机路径。新版会在导入会话时映射 `cwd`，重启后再恢复任务名称和项目归属，并完成原生验证。

导出文件夹包含敏感的聊天内容、代码、命令输出、路径和可能的凭据。仅通过可信渠道传输，并在迁移完成后清理不需要的副本。

## 恢复边界

skill 可以保留已导出的历史会话记录，并恢复所选任务的显示标题、映射后的工作路径和本地项目分组，但不能保证导入后产生逐字相同的未来回答。模型版本、工具、权限、外部文件和后续记忆仍可能因设备而不同。仅由路径引用且未嵌入会话的外部工作区文件不会被复制。

## v1.1 更新

本版来自一次四任务跨设备迁移的实际故障复盘，补充了 UTF-8 清单读取、大文件保真要求、路径映射、选定项目元数据快照、显示标题恢复、关闭 Codex 后的备份式索引修复、项目创建/归属恢复以及最终端到端验证。`cct --reconcile` 仍被视为尽力而为，不再作为“恢复成功”的唯一依据。

## v1.2 更新

- 默认共用迁移目录，支持自动新建导出包、包列表和多包选择。
- 修复 `preview` 缺失导致列表/导出失败，恢复正确显示标题，去重并过滤子代理。
- 内置基于 cct v2.0.0 的 `large512.1` 构建：单会话上限 512 MiB，总解压上限仍为 2 GiB。原始 398 MiB 会话已实际验证，不删图片、不裁剪聊天。
- 支持新版 `threads.name`、原生项目表以及旧版项目配置；恢复过程保持备份、关闭应用和验证要求。
- 修复坚果云文件夹被 PowerShell 误判为重解析点、路径映射未传递给差异检查、记忆 Skip/隔离冲突验证等问题。

验证：`python -m unittest discover -s tests -v`。Windows 集成测试需要 PowerShell 7，可用 `CCT_TEST_PWSH` 指定其路径；测试只使用隔离目录。构建来源、补丁和哈希见 skill 的 `references/third-party-notices.md`。

## 第三方组件

底层会话封装与验证使用 MIT 许可的 [`cct v2.0.0`](https://github.com/ahmojo/codex-claude-transfer/releases/tag/v2.0.0)。版本、哈希和完整许可文本见 [`third-party-notices.md`](skill/codex-chat-transfer/references/third-party-notices.md)。

## 许可

本仓库原创部分使用 MIT License。第三方组件适用其各自许可。
