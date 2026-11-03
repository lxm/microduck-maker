# 已验证音频构建输入

`verified-audio-inputs.tar.gz` 保存此前已实机发声验证的三个 ARM64 模块、对应基础内核的 depmod 索引和 `MODULES.json` 来源记录，供镜像构建复用。内核固定为 `6.6.98-vendor-sun60iw2`，不用于其它内核。

先按 `SHA256SUMS` 校验压缩包，再解包。镜像构建器继续检查模块/索引哈希、基础内核模块树摘要，以及补丁后源码和 Makefile 的摘要。更新输入时需重新生成完整来源记录和校验值，不能只替换 `.ko`。

Actions 仅复用包中的 `.ko` 和源码来源记录；使用 `REINDEX_MODULES=1` 在 Linux 上从固定底包重新生成 depmod 索引。包内原索引及模块树摘要来自此前 macOS 离线集成，不能直接套到包含大小写不同文件名的 Linux 模块树。

对应源码固定版本和每个文件哈希见 [`../sources.json`](../sources.json)，修改见 [`../vendor-to-module.patch`](../vendor-to-module.patch) 和 [`../prepare.py`](../prepare.py)，独立模块构建见 [`../../scripts/build_modules.sh`](../../scripts/build_modules.sh)，GPL 许可见 [`../COPYING`](../COPYING)。这些模块的源码准备结果与该构建流程一致；这次加入 Actions 没有重新编译音频。

```sh
# 在 firmware/orangepi-zero3w 目录执行
(cd audio/prebuilt && sha256sum --check SHA256SUMS)
mkdir -p .build/audio
tar -xzf audio/prebuilt/verified-audio-inputs.tar.gz -C .build/audio
```
