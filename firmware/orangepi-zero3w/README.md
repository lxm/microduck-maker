# Orange Pi Zero 3W 底层适配

本目录适配 **Allwinner A733 的 Orange Pi Zero 3W**，保留现有 HAT，通过设备树和驱动配置提供应用原有接口。机器人程序、FT6 协议、中控、模型、标定和应用服务均不修改。

This experimental BSP targets the Allwinner A733 Orange Pi Zero 3W. `make image` builds a flashable IMG/XZ from pinned Armbian, the existing application release and matching audio modules. Application code is unchanged. Audio compilation is a separate, explicit `make audio` target.

## 下载镜像

摄像头版镜像请从 [Orange Pi 固件 Actions](https://github.com/lxm/microduck-maker/actions/workflows/orangepi-image.yml) 的成功构建下载。下方固定发布版 exp.1 早于摄像头适配。

[下载 exp.1 完整刷卡镜像（.img.xz）](https://github.com/lxm/microduck-maker/releases/download/orangepi-zero3w-exp.1/Microduck_OrangePiZero3W_Experimental.img.xz) · [SHA-256 校验文件](https://github.com/lxm/microduck-maker/releases/download/orangepi-zero3w-exp.1/SHA256SUMS)

基于 [kissqy/Microduck-kissqy（`d4d21c1`）](https://github.com/kissqy/Microduck-kissqy/tree/d4d21c1073d2c12ad2b5dfe3deb031030f7792ef)，保留原有机器人应用、模型和 ToF 源码，由 MicroDuck Maker 在 Armbian 26.8.1 上适配串口、HAT I²C 和音频。 用 Armbian Imager 将校验后的镜像写入备用 TF 卡；本发布镜像已离线校验，尚未上板启动验证。

## 固定基线

- 实测硬件：1 GB、PCB V1.5；Armbian 26.8.1 / Debian 13，ARM64 内核 `6.6.98-vendor-sun60iw2`。
- 基础 DTB：`/boot/dtb/allwinner/sun60i-a733-orangepi-zero3w.dtb`，SHA-256 为 `1eb1160f92dd1d566af920002583c67fbbb6eb35d190a62fb8f0bc1236bb74e1`。
- 整卡镜像已包含 A733 Armbian 和机器人应用；仅单独运行板上安装器时才需要预装系统和应用。原项目 Radxa ZERO 3W 的 RK3566 镜像不能直接用于此板。
- 1 GB 板的 boot0 使用 [Armbian 修正版本](https://github.com/armbian/build/commit/17e3295e7d98ce7ae217437e919ce23b7ec28e21)。已验证 blob 的 SHA-256：`5ec7f17be1f2f653496529f0b2cd19a18977d4d122d458969a7cd855ab6d35fb`。本安装器不写入 boot0，也不制作或刷写整盘镜像。

## 必需改动

| 文件 | 作用 |
| --- | --- |
| `overlays/microduck-zero3w-kissqy-uart.dts` | 物理 8/10 脚 UART0 注册为 `/dev/ttyS2`；保留硬件 serial alias；禁用占用 tty line 2 的 UART2 |
| `overlays/microduck-zero3w-uart48m-v2.dts` | UART0 和蓝牙 UART1 同时指定共享 APB-UART 时钟 48 MHz，避免探测顺序影响缓存时钟 |
| `overlays/microduck-zero3w-hat-i2c.dts` | 启用物理 3/5 脚 TWI0，400 kHz，供 ToF 和音频编解码器使用 |
| `overlays/microduck-zero3w-audio-modules.dts` | AIC3104、独立 12 MHz codec 时钟及 I²S0 PB5–PB8；释放 PB4，禁用冲突的 TWI1 |
| `config/70-microduck-hat-i2c.rules` | 按控制器地址提供 `/dev/i2c-pihat` |
| `config/microduck-audio.conf` | 开机加载既有音频模块 |
| `audio/` | 固定上游版本、必要驱动补丁、源码准备脚本和许可；不复制整份上游驱动源码 |
| `install.py` | 校验基线、编译并合并检查 DTS、备份和安装底层文件，关闭 Linux 串口控制台及相关 getty |
| `Makefile`、`build_image.py`、`image_fs.py` | 仓库内生成完整 IMG/XZ，写入 1 GB boot0 修正、BSP 和现有应用，逐文件读回校验及压缩校验 |
| `scripts/`、`profile.json` | 获取固定底包、提取内核构建输入，以及可选的音频/现有 ToF 源码构建入口 |

时钟 ID `46`、`5` 来自固定内核的 `sun60iw2-ccu` binding，不适用于任意版本内核。安装器检查内核版本、板型、基础 DTB 和模块 vermagic；已有其它 overlay 时拒绝覆盖，需先审核引脚和时钟冲突。

## 构建完整刷卡镜像

### GitHub Actions 构建和下载

[`Orange Pi Zero 3W image`](../../.github/workflows/orangepi-image.yml) 工作流在本仓库 `main` 分支相关文件推送时自动运行。打开仓库 **Actions → Orange Pi Zero 3W image → 成功的运行记录 → Artifacts**，下载 `orangepi-zero3w-img-<运行编号>-<尝试编号>`。解压 artifact ZIP 后得到完整 `.img.xz`、`SHA256SUMS` 和构建记录；校验后可直接刷卡。下载保留 30 天，需要时可在已有运行页面选择 **Re-run all jobs** 重新构建。

工作流校验并复用 [`audio/prebuilt/`](audio/prebuilt/) 中的既有音频模块，使用仓库原 control.43 发布包，从现有源码构建 ToF，然后调用 `make image REINDEX_MODULES=1`。Linux runner 对固定底包的完整模块树重新运行离线 `depmod`，不会重编音频。没有私人 Wi-Fi、登录凭据或本机标定注入。只生成下载产物，不发布 Release 或创建 PR。

也可以在 [Actions 页面](https://github.com/lxm/microduck-maker/actions/workflows/orangepi-image.yml) 选择 **Run workflow → main**，无需推送新提交。

### 本地构建

镜像基于已适配 A733 的 Armbian，不重建完整内核或 U-Boot。工作目录均为本目录；推荐 Linux，依赖 `python3 make device-tree-compiler e2fsprogs xz-utils`。macOS 可使用 Homebrew 的相应工具，并把 `e2fsprogs/sbin` 加入 PATH。以下命令面向 Linux，需安装 Git、kmod，以及下文 ToF 所需的 Rust/Zig 工具链；确切版本见工作流。默认摄像头构建需 Linux、sudo、loop 挂载、网络和至少 20 GB 临时空间。x86 Linux 需注册 arm64 的 qemu-user-static/binfmt（见工作流）。构建脚本在独立挂载命名空间中安装 Debian trixie 的签名运行包，并使用镜像自己的 headers 编译 v4l2loopback。设置 CAMERA=0 可使用原来的免挂载构建。

```sh
make fetch
(cd audio/prebuilt && sha256sum -c SHA256SUMS)
mkdir -p .build/audio
tar -xzf audio/prebuilt/verified-audio-inputs.tar.gz -C .build/audio
make tofd
make image REINDEX_MODULES=1
```

`make tofd` 和 `make image` 自动获取 [upstream.rev](upstream.rev) 指定的 [kissqy 原仓库](https://github.com/kissqy/Microduck-kissqy) 提交，应用包、ToF 和声卡初始化脚本均取自该提交，不依赖 fork 分支。

`make fetch` 按 `profile.json` 的固定 URL 下载、校验和解压底包及 boot0。已有文件可通过 `BASE_IMAGE=/path/to/base.img BOOT0=/path/to/boot0.fex` 传入 `make image`，省去重复下载。

`MODULES` 目录必须包含 `modules/` 下的三个 `.ko`、`indexes/` 下对应基础内核的 depmod 索引，以及 `MODULES.json`（内核、原始模块树、补丁后源码和所有产物的 SHA-256）。只有三个裸 `.ko` 可用于上面的单板安装器，但不足以离线生成镜像索引。此次镜像沿用已验证模块及索引，不执行音频编译。

Linux 构建推荐安装 `kmod` 并传入 `REINDEX_MODULES=1`，直接对固定底包及校验过的 `.ko` 生成索引，不依赖输入包中的旧索引。底包存在仅大小写不同的模块名（例如 `xt_DSCP.ko` / `xt_dscp.ko`），macOS 默认文件系统导出的模块树不适合作为 Linux 索引基线。新的 `MODULES.json` 记录实际使用的模块树及索引摘要。

默认输出：

```text
.build/image/release/Microduck_OrangePiZero3W_Experimental.img
.build/image/release/Microduck_OrangePiZero3W_Experimental.img.xz
.build/image/release/SHA256SUMS
.build/image/BUILD.json
.build/image/validation/image-build.json
```

输出目录必须是新目录；再次构建可指定 `OUTPUT=.build/image-next`。用 Armbian Imager 的自定义镜像选项写入备用 TF 卡并完成写后校验；建议保留原卡和本机标定。需要回退时断电换回原卡。镜像包含原 control.43 应用包、模型、ONNX Runtime、ToF、音频初始化和手动启动的服务；运行配置直接复制仓库原 profile 和未标定模板，不使用应用包中附带的参考机器标定。应用包内容逐字节保留，FT6 不重新烧录。

首次登录沿用 Armbian 原机制，可使用 USB 有线网络或 HDMI 本地配置。仅有 Wi-Fi 时，构建参数 `WIFI_NETPLAN=/private/path/wifi.yaml` 可注入用户提供的有效 Netplan 配置；此时产物包含私有配网信息，勿作为公开镜像分发。没有另加账号/配网守护进程，SSH host key 由 Armbian 首次启动重新生成。新镜像首次登录和联网尚待上板验证。

镜像从固定的 kissqy 提交原样安装 `robotd.service` 和 `tofd.service`，板级差异由 systemd 的 `10-orangepi.conf` 补充。它们指定实际的程序路径、舵机只读启动和 HAT I²C 参数，保留手动启动及现有 ToF 运行身份（root、robot 组）。原中控写入的 R17 配置可以覆盖初始的只读启动命令。机器人服务不自动启动。完成首次登录后可执行 `sudo systemctl start robotd tofd` 进行只读检查；之后使用原中控完成本机标定及使能。摄像头使用原版 mediad，见下方启动步骤。

### IMX219 摄像头和 WebRTC

关机接好 CSI 排线，再启动板子。摄像头服务按需启动：

```sh
sudo systemctl start mediad
curl -f http://127.0.0.1:8080/frame -o /tmp/camera.png
```

在电脑执行 `ssh -N -L 8080:127.0.0.1:8080 -L 8443:127.0.0.1:8443 USER@BOARD`，浏览器打开 <http://127.0.0.1:8080> 并点 Connect。默认 720p30；在 `/etc/microduck/vision.toml` 将 quality 改为 `1080p30` 后执行 `sudo systemctl restart mediad`。用 `sudo systemctl stop mediad` 同时关闭采集。

V4L2 的 `/dev/video0` 采集单平面 NV12，板级转换器将 UYVY 帧送到 `/dev/video42` 供原版 mediad 使用。镜像包含 GStreamer、x264、kissqy R1 的 WebRTC 插件，以及 [Pollen Robotics 的 duck-v1 ONNX 权重（Apache-2.0）](https://huggingface.co/pollen-robotics/microduck-duck-detector/tree/eeda8d200e4e91490d09b55c94648773406cc416)。原版 ONNX Runtime 在 CPU 执行检测；实验用的 A733 NPU 后端独立于本镜像。输入来源和 SHA-256 见 [camera/fetch.py](camera/fetch.py)。

### 从源码更新构建输入

- **机器人应用**：沿用仓库现有 [V167 构建流程](https://github.com/kissqy/Microduck-kissqy/blob/d4d21c1073d2c12ad2b5dfe3deb031030f7792ef/microduck-hd1910/firmware/BUILDING-V167.md)，无需 Orange Pi 分支逻辑。把新生成的 `Microduck-Robotd-FT6-R17.tar.gz` 通过 `APPLICATION=/path/to/archive APPLICATION_SHA256=<sha256>` 传入 `make image`。不传时使用固定哈希的仓库发布包；应用变化必须显式提供新包和摘要。
- **ToF**：按原 [Rust/Zig 工具链说明](https://github.com/kissqy/Microduck-kissqy/blob/d4d21c1073d2c12ad2b5dfe3deb031030f7792ef/microduck-hd1910/docs/BUILD.md) 安装依赖后执行 `make tofd`，构建现有 `firmware/tof`，输出即默认 `TOFD` 路径。
- **音频**：可持续复用既有模块；未来需重编时，准备 `audio/sources.json` 指定 commit 的内核源码树，在原生 ARM64 Linux 上安装 `build-essential kmod binutils python3 patch`，执行：

```sh
make kernel-inputs
make audio KERNEL_SOURCE=/path/to/linux-orangepi
make tofd
make image
```

`kernel-inputs` 从固定底包提取 headers 和原模块树；`audio` 校验配置/符号表，应用补丁后构建模块、运行离线 depmod 并生成来源记录。`make image` 不依赖 `audio` 编译目标，所以不会隐式重编。此次仅验证了源码准备及镜像构建，未执行该音频编译目标。

## 安装（复用已有音频模块）

板上需有 `python3`、`device-tree-compiler`、`kmod`。准备同一内核及配置下已验证的三个模块：

```text
snd-soc-tlv320aic3x.ko
snd-soc-tlv320aic3x-i2c.ko
snd-soc-microduck-aic3104.ko
```

在本目录执行，参数为上述三个 `.ko` 所在目录；已适配的系统可直接复用原模块目录：

```sh
sudo python3 install.py /lib/modules/6.6.98-vendor-sun60iw2/updates/microduck-audio
```

已有模块及来源记录以校验过的压缩包保存在 `audio/prebuilt/`，可直接解包使用；需要重编时走上述独立音频构建目标。此次沿用既有模块，不重新编译音频。

安装器仅写入 `/boot/overlay-user`、`/boot/armbianEnv.txt`、模块目录、模块加载配置、I²C udev 规则及相关 serial-getty mask。保留 root UUID 和其它启动参数，删除 Linux 串口 console/earlycon 参数。不会修改账号、Wi-Fi、标定或应用配置，也不会当场加载驱动、停止运动服务或重启。

每次安装先备份至打印出的 `/var/backups/microduck-zero3w-*`，`state.json` 记录每个路径原先是否存在。安装报错会恢复受管文件并重新运行 `depmod`；启动配置最后写入。若需事后回退，按该清单还原备份文件及符号链接，删除原先不存在的受管文件，再执行 `sudo depmod -a 6.6.98-vendor-sun60iw2`。

安装或回退后，先承托并卸力机器人，再完整断电重新上电。配置在下次启动生效，当前热重启可靠性仍需排查。

## 驱动源码

来源及原文件/补丁后文件的 SHA-256 见 [`audio/sources.json`](audio/sources.json)。上游固定为 `orangepi-xunlong/linux-orangepi` 的 `2ac08e8c7cdc28abbdc5c9a9dd812f887ae9c79f`。

未来需要维护驱动时，提供该版本的本地内核源码，执行：

```sh
python3 audio/prepare.py /path/to/linux-orangepi /tmp/microduck-audio-src
```

该命令只校验、复制和应用补丁，不调用编译器。原 787 行补丁中，118 对修改行仅做 `asoc_simple_*` → `microduck_simple_*` 的统一替换；现由准备脚本集中处理，最后仍按原哈希验证每个生成文件，保持已验证源码逐字节一致。这缩小的是维护和审阅内容，不是编译后的驱动体积。

剩余 **91 行功能补丁（+18 / -5，含上下文）** 保留以下行为：

| 改动 | 在当前独立模块方案中的作用 |
| --- | --- |
| 独立 codec sysclk 属性与设置 | HAT codec 使用自身 12 MHz 振荡器，CPU MCLK 输出关闭，不能再把 CPU 的 0 Hz 直接给 codec |
| 可选跳过 codec TDM 设置 | vendor machine 传入的 TX/RX mask 为 0，会被 AIC3x 的 TDM 回调以 `-EINVAL` 拒绝；普通 I²S 不调用该回调，CPU 端仍配置时隙 |
| 独立 driver name / compatible | 只绑定本 HAT 节点，与原 vendor machine driver 共存 |

结构体、静态函数和日志文字的全部重命名并非逐项硬件必需；目前保留统一隔离后的已验证结果，避免在未重编/重测时改变符号行为。未修改 `tlv320aic3x*.c` codec 驱动本体。若改为重编并替换整个内核，可重新评估命名隔离，但会扩大本次适配范围。GPL 许可见 [`audio/COPYING`](audio/COPYING)。

## 验证范围与待办

- 原适配实机已验证 UART 回环（30 秒、9,458 轮、2,402,332 字节、零错误）、HAT 通信、站立/行走和音频播放；新的构建产物仍需上板验证，离线镜像检查不代表实机验收。
- 完整舵机总线偶发校验错误、热重启后联网问题仍需排查。boot0/U-Boot 早期串口输出未静默；本配置仅关闭 Linux 阶段串口控制台。
- IMX219 在测试卡上通过实拍及浏览器解码验证。720p30、1080p30 在 Mac Chrome 的短时解码实测分别约 29.9、26.8 fps。新构建镜像需单独刷卡验证。
