# 复旦大学成绩自动监控与推送 (Fudan Grades Monitor)

这是一个基于 Python 和 GitHub Actions 的自动化工具，用于定时抓取复旦大学（fdjwgl.fudan.edu.cn）的个人成绩，并在出分或绩点变化时自动发送邮件通知。

> 如果有用，欢迎点一下star⭐

## 核心功能

*   **WebVPN 接入（默认开启）**: 复旦教务系统现已限制需校内网访问。本工具默认通过 WebVPN（`webvpn.fudan.edu.cn`）访问，因此部署在 GitHub Actions 等校外环境也能正常工作。校内运行可设 `USE_DIRECT=1` 走直连。
*   **自动抓取**: 模拟登录复旦统一身份认证（UIS RSA 加密），自动完成教务系统的单点登录（SSO）。
*   **成绩 / GPA / 排名**:
    *   抓取全部学期的课程成绩，学分已由接口直接返回，无需额外查询。
    *   直接读取官方 GPA 统计接口（已正确剔除 P/NP 与重修）。
    *   读取**院系排名**（零配置）和**专业排名**（需配置 `MAJOR_ASSOC`）。
*   **隐私安全**:
    *   成绩快照保存为 `grades_encrypted.json`。
    *   **加密存储**: 用你的凭据派生的密钥进行 Fernet（AES）加密，文件即便公开，没有你的密码也无法解密。
    *   敏感信息（学号、密码、邮箱授权码）均通过 GitHub Secrets 注入，不直接出现在代码中。
*   **邮件推送**: 一旦检测到新成绩 / GPA 变化 / 排名变化，立即向你的复旦学邮（`学号@m.fudan.edu.cn`）发送通知。邮件主题会区分“好消息”（GPA 上升）、“坏消息”（GPA 下降）或普通更新。
*   **无人值守 + 自动收敛**: 部署在 GitHub Actions 上每小时运行。**数据无变化时不写文件、不 commit**，仓库保持静默；出分季结束后 GitHub 会按其设置自动停用定时任务，避免浪费公共算力。

## 使用指南 (How to Use)

只需简单几步，你就可以拥有自己的成绩监控机器人。

### 1. Fork 本仓库
点击页面右上角的 **Fork** 按钮，将本项目复制到你的 GitHub 账号下。

### 2. 配置 GitHub Secrets
进入你 Fork 后的仓库，点击 **Settings** -> **Secrets and variables** -> **Actions** -> **New repository secret**，添加以下变量：

| Secret Name | 必填 | 说明 | 示例值 |
| :--- | :---: | :--- | :--- |
| `STUDENT_ID` | ✅ | 你的复旦学号 | `23300123456` |
| `UIS_PASSWORD` | ✅ | 你的 UIS 登录密码 | `MySecretPass123` |
| `QQ_EMAIL_SENDER` | ✅ | 发送通知的 QQ 邮箱地址 | `12345678@qq.com` |
| `QQ_SMTP_AUTH_CODE` | ✅ | QQ 邮箱的 SMTP 授权码* | `abcdefghijklmnop` |
| `MAJOR_ASSOC` | ⬜ | 你的专业内部 ID，用于专业排名（院系排名无需配置） | `419` |

> **如何获取 QQ SMTP 授权码**:
> 登录 QQ 邮箱网页版 -> 设置 -> 账号 -> 向下滚动找到 "POP3/IMAP/SMTP/Exchange/CardDAV/CalDAV服务" -> 开启服务 -> 生成授权码。

> **如何获取 `MAJOR_ASSOC`**:
> 登录教务系统 → 我的成绩 → 我的绩点实时查询 → 切到「按专业」，按 F12 打开开发者工具 → Network，找到 `my-gpa/search` 请求，其 query 中的 `majorAssoc=` 后的数字即为你的专业 ID。不配置则只推送院系排名。

### 3. 开启写入权限 (重要)
**这是程序能够自动更新成绩记录文件的关键步骤，请务必执行：**
1.  在仓库页面点击 **Settings**。
2.  在左侧栏点击 **Actions** -> **General**。
3.  向下滚动找到 **Workflow permissions** 区域。
4.  选中 **Read and write permissions**。
5.  点击 **Save** 保存。

### 4. 启动监测
配置完成后，GitHub Actions 默认会按照计划（每小时）自动运行。
你可以手动触发第一次运行来初始化数据：
1.  点击仓库上方的 **Actions** 标签。
2.  在左侧选择 **Fudan Grades Monitor**。
3.  点击右侧的 **Run workflow** 按钮 -> **Run workflow**。

### 5. 运行结果
*   **第一次运行**: 会抓取当前所有成绩并加密保存，发送一封包含 GPA / 排名的初始化摘要邮件（不会把历史所有课程列出来刷屏）。
*   **后续运行**: 每小时自动检查。**有新成绩 / GPA 变化 / 排名变化时**，更新加密文件、commit、并发邮件通知；**无变化时不写文件、不 commit**，仓库保持静默。

## 本地运行

```bash
pip install requests pycryptodome cryptography

# 默认走 WebVPN（校外也能跑）
StuId=你的学号 UISPsw=你的密码 QQ_EMAIL_SENDER=... QQ_SMTP=... python crawl_grades.py

# 校内网络可走直连，更快
USE_DIRECT=1 StuId=... UISPsw=... python crawl_grades.py
```

## 技术流程原理

1.  **环境初始化**: GitHub Actions 启动 Ubuntu 容器，安装 Python 依赖（`requests`, `pycryptodome`, `cryptography`）。
2.  **登录**（默认 WebVPN 模式）:
    *   对 `webvpn.fudan.edu.cn` 完成 7 步 IDP 认证，建立 VPN 会话。
    *   通过 WebVPN 触发教务系统的 SSO 重定向，完成 fdjwgl 的统一身份认证（RSA 加密密码）。
    *   直连模式（`USE_DIRECT=1`）跳过 VPN 网关，直接完成 fdjwgl 的 SSO。
3.  **数据抓取**（`GradeClient`）:
    *   动态探测个人成绩单 ID。
    *   `info/{id}` 取全部成绩（含学分）；`grade-statistic/{id}` 取官方 GPA；`my-gpa/search` 取排名。
4.  **变化检测**:
    *   解密旧的 `grades_encrypted.json`（若存在）。
    *   对比课程成绩、GPA、院系 / 专业排名。
5.  **通知与存储**:
    *   **有变化**：发邮件，并把新快照加密覆盖写入 `grades_encrypted.json`，commit 回仓库。
    *   **无变化**：不写文件、不 commit —— 仓库保持静默，让 GitHub 在出分季结束后按设置自动停用定时任务。

## 免责声明
本项目仅供学习交流使用。请勿用于非法用途或高频恶意请求学校服务器。使用本工具产生的任何后果由使用者自行承担。
