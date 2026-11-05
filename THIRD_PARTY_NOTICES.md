# 来源与许可范围

仓库根目录 MIT 许可证适用于 lxm 原创界面、维护逻辑、脚本和说明，不覆盖本页列出的另行许可代码、第三方代码、几何模型或 CAD 衍生资源。

## 颈部连接板

`tools/neck-interconnect/d0.8.1/` 是维护者整理的原创 PCB、简化板级接线模型和说明，适用根目录 MIT。原设计参考 HD-1910 厂家装配尺寸及 [CJT A2007 系列资料](https://www.cjt.com/upload/A2007.pdf)；本次不捆绑厂家舵机 CAD。原配胶壳／短线为简化示意模型，不指代供应商精确模型或替代件认证。来源清单、哈希、整理前后差异与实物待验证项见该目录的 [资料来源](tools/neck-interconnect/d0.8.1/docs/provenance.md)。

## 标定工具

`tools/calibrator/` 收录现有 HD1910 标定工具的公开整理版本。原创的 Python 维护程序、外层 `static/app.js`、`view.js`、`app.css` 和 `index.html` 使用根目录 MIT 许可。

### Kissqy 参考代码

- 作者/来源：[kissqy/Microduck-kissqy](https://github.com/kissqy/Microduck-kissqy)。
- 固定提交：`3d2b210dae8cd78b20f090fe7bed8c6a49bb7108`。
- 引用范围：`microduck-hd1910/firmware/console/` 中的 3D 姿态、关节映射与显示支持文件。
- 本仓库位置：`tools/calibrator/static/source/`；11 个引用资源的原始哈希见相邻 `source-manifest.json`。
- 上游原创参考代码保留其**个人非商业使用许可**，全文是 `static/source/LICENSE-console`。允许范围、分享条件及商业/组织使用所需授权以该原文为准。
- 既有上游第三方声明原样保存在 `static/source/THIRD_PARTY_NOTICES.md`。该文件同时记载了原控制台中未被本项目收录的组件，不代表本仓库捆绑完整固件、音源或训练权重。

这些引用文件内容未修改。本次新增的是独立项目目录、使用与兼容性说明、来源校验和发布资料。外层标定工具不会因上游参考代码的个人许可而重新授权上游 Apache/MIT 程序，根 MIT 也不会覆盖上游个人非商业条款。

### 3D 几何模型

`tools/calibrator/static/source/assets/duck-visual.bin` 及其来源元数据来自上述 Kissqy 固定版本。几何模型归 Pollen Robotics / Microduck 模型贡献者：

- 原始模型：[pollen-robotics/microduck_rl](https://github.com/pollen-robotics/microduck_rl/tree/e8a2de510b6cb5062e9ccab8b41f2d47f2186504/src/mjlab_microduck/robot/microduck)。
- 来源、哈希和坐标适配记录保留在 `duck-visual.json` 与 `static/source/licenses/MODEL-NOTICE.md`。
- 沿用随文件提供的署名、非商业、相同方式共享要求。原引用声明未指定 Creative Commons 版本，本项目不另行指定版本，也不将几何模型归入 MIT 或 Apache-2.0。
- 所附 `MICRODUCK-APACHE-2.0.txt` 保留对官方软件来源的许可记录，不是几何模型的替代许可。

### Three.js

标定工具引用 Three.js **0.180.0** 与 OrbitControls，作者为 three.js authors，MIT。原许可位于 `tools/calibrator/static/source/licenses/THREE-MIT.txt`，资源固定哈希位于 `source-manifest.json`。

## 舵机装配对照工具

`tools/servo-guide/` 收录自 [lxm/microduck-servo-guide](https://github.com/lxm/microduck-servo-guide)，原始快照提交 `38da88250c3a515201ea6d3e50be441f6dc655ed`。原创网页和提取/验证脚本采用该目录保留的 MIT 许可证。

- CAD 作者：**机械行者Robo**。
- 发布/适配来源：[fanhao375/microduck-replica-cad v2.1](https://github.com/fanhao375/microduck-replica-cad/releases/tag/v2.1)，固定附件 `SolidWorks-FT2026-09-25.zip`。
- 底层机器人模型：**Pollen Robotics / microduck_rl**。
- `tools/servo-guide/site/model/` 的显示网格、装配变换及标注数据采用 **CC BY-NC-SA 4.0**；原声明和完整许可分别保留于该工具的 `THIRD_PARTY_NOTICES.md` 和 `site/licenses/CC-BY-NC-SA-4.0.txt`。
- Three.js **0.160.0** 与 OrbitControls 保留 MIT 许可，见该工具 `site/vendor/THREE-LICENSE.txt`。
- 本次只调整仓库入口说明和 CI 存放位置，模型与网页资源保持原内容，现有在线站点继续由原仓库维护。

本项目未改变上述作者的署名、商标权和各自许可，也不暗示其对本项目的背书。

## 零位标定支架

`tools/zero-fixture/stl/` 的 6 个打印模型及 `images/assembly-reference.png` 由交流群分享的 `0位支架.stp` 导出，维护者负责整理。目前没有可提供的原始发布链接，原始设计作者和模型许可尚未确认；该设计不署名为维护者原创，群内分享不代表已获开源授权。STL 仅作网格化与平移，没有简化或配合修正；源文件与导出文件的哈希记录在该目录的 `manifest.json`。

不能根据同属 MicroDuck 项目就推断它与装配对照模型采用相同许可。上述几何模型和截图不纳入根目录 MIT 许可，本项目不另行授予模型使用权。原作者或权利人可通过 [仓库 Issues](https://github.com/lxm/microduck-maker/issues) 联系维护者补充署名、明确许可或要求移除；具体来源状态见 [支架说明](tools/zero-fixture/README.md#来源与许可)。

## Orange Pi Zero 3W 固件

[板级适配](firmware/orangepi-zero3w/) 基于 Armbian 并复用 kissqy 的机器人发布包、ToF 和模型，按[上游许可范围](https://github.com/kissqy/Microduck-kissqy/blob/d4d21c1073d2c12ad2b5dfe3deb031030f7792ef/microduck-hd1910/LICENSE)分别授权。基础镜像和 boot0 的来源与哈希见 [profile.json](firmware/orangepi-zero3w/profile.json)。Linux 音频模块的源码版本、补丁和 GPL 许可见 [audio/](firmware/orangepi-zero3w/audio/)；这些第三方组件及其构建产物不适用本仓库根 MIT 许可。

## 硅胶脚底翻模

模具与预览图衍生自 Pollen Robotics 的 Microduck 脚底几何，使用 fanhao375 整理的左右脚底 STL。衍生模型遵循 CC BY-NC-SA 的署名、非商业和相同方式共享条件。改动及固定来源见[模具说明](tools/silicone-sole-mold/README.md#几何检查与来源)。

## 飞特舵机调试工具

`tools/servo-debug/` 来自本地 microduck-replica 的独立 Mac 调试工具与 servo-web 串口协议模块。本次修改界面、静态资源组织与状态反馈，协议模块随目录打包。导入前基线与文件哈希见 [source-origin.json](tools/servo-debug/source-origin.json)。

继承代码保留原项目原创工具的 Apache-2.0 许可，新增界面改动沿用该许可，不归入本仓库根 MIT。原声明与许可全文见 [SOURCE-LICENSE.txt](tools/servo-debug/licenses/SOURCE-LICENSE.txt) 和 [Apache-2.0.txt](tools/servo-debug/licenses/Apache-2.0.txt)。工具 README 为新写的使用说明，适用根 MIT；原项目文档未打包。本工具不捆绑官方 FD、驱动、厂商 SDK 或机器人模型。pyserial 3.5 通过依赖安装，保留其 BSD 许可。
