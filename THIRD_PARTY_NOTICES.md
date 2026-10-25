# 来源与许可范围

仓库根目录 MIT 许可证适用于 lxm 原创界面、维护逻辑、脚本和说明，不覆盖本页列出的第三方代码、几何模型或 CAD 衍生资源。

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
