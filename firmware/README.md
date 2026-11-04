# 固件

## Orange Pi Zero 3W（Allwinner A733）

基于 [kissqy/Microduck-kissqy（`d4d21c1`）](https://github.com/kissqy/Microduck-kissqy/tree/d4d21c1073d2c12ad2b5dfe3deb031030f7792ef)，保留原有机器人应用、模型和 ToF 源码，由 MicroDuck Maker 在 Armbian 26.8.1 上适配串口、HAT I²C 和音频。

摄像头版镜像请从 [Orange Pi 固件 Actions](https://github.com/lxm/microduck-maker/actions/workflows/orangepi-image.yml) 的成功构建下载。下方固定发布版 exp.1 早于摄像头适配。

[下载 exp.1 完整刷卡镜像（.img.xz）](https://github.com/lxm/microduck-maker/releases/download/orangepi-zero3w-exp.1/Microduck_OrangePiZero3W_Experimental.img.xz) · [SHA-256 校验文件](https://github.com/lxm/microduck-maker/releases/download/orangepi-zero3w-exp.1/SHA256SUMS) · [板级源码与构建说明](orangepi-zero3w/)

下载镜像和校验文件到同一目录，执行 `shasum -a 256 -c SHA256SUMS`。校验通过后，在 Armbian Imager 选择自定义镜像，写入 8 GB 或更大的备用 TF 卡。目标硬件为 1 GB、PCB V1.5；本发布镜像已通过离线校验，尚未上板启动验证。

## 收录约定

新增固件时按 `firmware/<板型或组件>/` 分目录，每个目录至少说明：

- 支持的板型、硬件版本、舵机/IMU 和通信接口。
- 上游来源、固定提交、修改内容及各自许可。
- 构建依赖、完整构建命令与校验方法。
- 安装/升级步骤、配置与标定保留策略、回退方法。
- 已完成的实机验证和未覆盖的条件。

预编译镜像和升级包放到 GitHub Releases，目录内保留源码、构建说明和校验清单。设备密钥、真实机器标定、个人网络配置和运行日志不进入公共仓库。
