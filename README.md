# Codex Chat Transfer Skill

一个面向 Windows x64 的本地 Codex skill，用于选择性导出多个聊天，并在另一台电脑上安全预检、导入和恢复这些聊天。

## 功能

- 列出并选择多个用户聊天。
- 保留原生线程 ID、会话记录、上下文、工具事件和嵌入内容。
- 可携带全局 Markdown 记忆的时间点快照。
- 导入前验证路径、SHA-256、bundle 清单和本机差异。
- 相同聊天自动跳过；同 ID 的分叉聊天拒绝覆盖。
- 记忆只增加缺失文件；同名冲突进入隔离目录，不覆盖本机记忆。
- 默认检测并阻止可能包含凭据的导出。

它不会持续同步，不会复制整个 `.codex` 目录，不会上传 `auth.json`、SQLite、WAL/SHM、配置或日志，也不会创建后台监控、启动项或目录链接。

## 安装

```powershell
git clone https://github.com/dangerous090566/codex-chat-transfer-skill.git
Copy-Item -LiteralPath ".\codex-chat-transfer-skill\skill\codex-chat-transfer" -Destination "$env:USERPROFILE\.codex\skills" -Recurse
```

安装后重新启动 Codex。

也可以让 Codex 从本仓库安装 `skill/codex-chat-transfer`。

## 使用

导出：

> 使用 `$codex-chat-transfer` 列出我的聊天，让我选择若干个后导出。

导入：

> 使用 `$codex-chat-transfer` 检查这个迁移文件夹，确认安全后导入聊天和记忆。

导出文件夹包含敏感的聊天内容、代码、命令输出、路径和可能的凭据。仅通过可信渠道传输，并在迁移完成后清理不需要的副本。

## 恢复边界

skill 可以保留已导出的历史会话记录，但不能保证导入后产生逐字相同的未来回答。模型版本、工具、权限、项目路径和当前记忆可能因设备而不同。仅由路径引用且未嵌入会话的外部工作区文件不会被复制。

## 第三方组件

底层会话封装与验证使用 MIT 许可的 [`cct v2.0.0`](https://github.com/ahmojo/codex-claude-transfer/releases/tag/v2.0.0)。版本、哈希和完整许可文本见 [`third-party-notices.md`](skill/codex-chat-transfer/references/third-party-notices.md)。

## 许可

本仓库原创部分使用 MIT License。第三方组件适用其各自许可。
