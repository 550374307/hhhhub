# 发版说明（本 fork）

## 版本号规则

`desktop/version.json` 是**唯一真相**，有两个字段：

| 字段 | 示例 | 用途 |
|---|---|---|
| `version` | `1.0.2` | 纯数字三段。仅供构建期数值换算（Windows `filevers`、Android `versionCode`），**不用于展示** |
| `release` | `1.0.2-fix3` | 对外展示版本。UI、更新比较、产物文件名、tag 校验全用它 |

tag 必须等于 `v` + `release`，由 workflow 强校验（大小写敏感）。

> 早期只把 fix 号写在 tag 上、`version` 固定跟随上游，导致安装包与界面里
> 永远显示 `1.0.2`，用户分不清装没装上新版，自动更新也永远判定「已最新」。
> 拆成两个字段就是为了堵住这个坑。

发新版本的步骤：

```bash
# 1) 改 release（version 只在跟进上游时改）
#    desktop/version.json  "release": "1.0.2-fix4"

# 2) 提交并推送
git add -A && git commit -m "..." && git push origin main

# 3) 打 tag 并推送（tag 会自动触发构建 + 发 release）
git tag -a v1.0.2-fix4 -m "v1.0.2-fix4"
git push origin refs/tags/v1.0.2-fix4
```

## 产物

| 目标 | 产物 | 说明 |
|---|---|---|
| Windows | `miraihub-<release>-windows-x64.exe` + `.sha256` | 每次必出 |
| Android | `miraihub-<release>-android-arm64.apk` + `.apk.sha256` | **需配签名 Secret**，否则跳过 |
| Docker | `ghcr.io/<owner>/<repo>:<tag>` 与 `:latest` | linux/amd64 + linux/arm64 多架构 |

## Android 签名（重要）

APK 必须用**同一个签名身份**跨版本构建，否则系统拒绝覆盖安装。CI 每次
都是全新环境，如果让它自己生成 keystore，每个 release 都会变成新身份 ——
用户无法升级，只能先卸载（会丢数据）。

所以需要两个仓库 Secret（Settings → Secrets and variables → Actions）：

| Secret | 内容 |
|---|---|
| `ANDROID_KEYSTORE_B64` | `release.jks` 的 base64 单行文本 |
| `ANDROID_KEYSTORE_PASS` | keystore 密码（同时作为 keyPassword） |

生成与导出的方法：

```bash
# 生成（alias 必须是 workbuddy2api，与 android/app/build.gradle 一致）
keytool -genkeypair -keystore release.jks -storetype JKS \
  -storepass '<你的密码>' -keypass '<你的密码>' \
  -alias workbuddy2api -keyalg RSA -keysize 3072 -validity 10000 \
  -dname 'CN=MiraiHub Android'

# 导出成 base64 单行
base64 -w0 release.jks > keystore.b64     # macOS: base64 -i release.jks -o keystore.b64
```

把 `keystore.b64` 的全部内容填进 `ANDROID_KEYSTORE_B64`。

> ⚠️ **`release.jks` 和密码务必自己备份**（离线保存）。丢了就无法再发布
> 「可覆盖安装」的新版 APK，已安装用户只能卸载重装。
> 这个文件绝不能提交到 Git（`.gitignore` 已排除 `build/android-signing/`）。

## 发布 job 的容错策略

- Windows 失败 → 中止发布（主力产物）
- Docker 失败 → 中止发布
- Android 缺席（没配 Secret）→ 只警告，其余照发

## Docker

镜像推送到 GHCR，用 `GITHUB_TOKEN` 鉴权，不需要额外 Secret。
公开拉取：

```bash
docker pull ghcr.io/550374307/hhhhub:v1.0.2-fix3
docker pull ghcr.io/550374307/hhhhub:latest
```

多架构由 QEMU + buildx 构建，`docker manifest inspect` 可确认同时含
`linux/amd64` 与 `linux/arm64`。
