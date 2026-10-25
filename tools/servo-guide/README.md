# MicroDuck Servo Guide

**飞特 HD-1910 版 MicroDuck 的 3D 舵机编号与装配对照。**

[在线主页](https://lxm.github.io/microduck-servo-guide/) · [直接打开 3D](https://lxm.github.io/microduck-servo-guide/assembly.html) · [反馈标注问题](https://github.com/lxm/microduck-servo-guide/issues)

围绕 fanhao375 飞特 v2.1 装配模型，为 15 颗舵机提供 ID、中文名称、所属组件和位置标注。面向自己打印、组装 MicroDuck 的爱好者。

- 旋转、缩放、正背面与侧面视角。
- 透明外壳 / 完整外形 / 仅显示舵机。
- 按左腿、右腿、头颈筛选，点击舵机或编号高亮。
- 安装勾选仅存于当前浏览器，可导出 CSV 编号表。
- 特别提示易互换的 **11/12、21/22**。
- 全部脚本、模型本地托管，无 CDN、分析追踪或串口连接。

## 快速对照

**左右始终按机器人自身定义。** 从背后看，左手边就是左腿。

| 位置 | 右侧 ID | 左侧 ID |
|---|---:|---:|
| 髋偏航，躯干底座内 | 10 | 20 |
| 髋侧摆，靠躯干的 yaw-roll 转接件 | 11 | 21 |
| 髋俯仰，大腿同板靠髋的一颗 | 12 | 22 |
| 膝，大腿同板靠膝的一颗 | 13 | 23 |
| 踝，小腿组件连接脚掌 | 14 | 24 |

头颈：30 颈部俯仰、31 头部俯仰、32 头部左右转、33 头部侧倾、34 嘴。

**这是一张位置与编号对照表，不会读取或写入实机 ID。** 装配姿态不是关节归零或站立姿态，不可据此直接执行整机动作。

## 模型版本与来源

- 源 CAD：[fanhao375/microduck-replica-cad v2.1](https://github.com/fanhao375/microduck-replica-cad/releases/tag/v2.1)。图纸作者：**机械行者Robo**。
- 固定附件：`SolidWorks-FT2026-09-25.zip`。
- 顶层：`00-microduck装配体-FT2026.9.23.SLDASM`，SHA-256 `dba8e6d82bc196046b2811f823394797e29768eb0e2e7746ec7e4aa9ed48d3f0`。
- 提取原生装配变换和保存显示网格：69 个零件实例、38 种网格、15 颗 HD-1910 单盘 / 双盘舵机。
- `soft_mouth_top_软嘴顶部-FT.SLDPRT` 没有可提取网格，因此软嘴顶部未显示；全部舵机均已提取。
- ID 采用 [fanhao375/microduck-replica 的项目关节编号](https://github.com/fanhao375/microduck-replica/blob/b5381d86b68d2e4f4606170d54d1f3f46d249ffc/tools/servo-web/server.py)，结合装配层级、左右侧及输出轴分配到 CAD 实例。标注 ID 并非从 CAD 或实机读取。
- 原始模型来源于 [Pollen Robotics / microduck_rl](https://github.com/pollen-robotics/microduck_rl)。独立社区工具，无官方隶属或背书关系。

## 本地使用

需要 Python 3 和支持 WebGL 的现代浏览器。无需 npm、构建步骤或 Python 第三方依赖。

```sh
git clone https://github.com/lxm/microduck-maker.git
cd microduck-maker/tools/servo-guide
python3 -m http.server 8772 --bind 127.0.0.1 --directory site
```

打开 `http://127.0.0.1:8772/`。首次下载 3D 网格约 20 MB；文件下载齐全后可离线运行。不要直接以 `file://` 打开 HTML，浏览器会限制模型加载。

## 项目结构

```text
site/
  index.html       # 公开主页与编号表
  assembly.html    # 3D 装配查看器
  credits.html     # 作者、来源、变更及许可
  model/           # 网格、实例矩阵、编号审计
  vendor/          # Three.js 0.160.0 / OrbitControls
scripts/
  build_model.py   # 原生 SolidWorks 显示网格提取
  validate.py      # 发布前静态资源与模型校验
docs/original-pages-workflow.yml  # 原 Pages 工作流参考
```

## 验证与发布

本工具已收录到 MicroDuck Maker，原仓库快照为 `38da88250c3a515201ea6d3e50be441f6dc655ed`。这里由根目录 CI 统一验证；以下是原仓库的 Pages 发布约定，原在线地址保持可用。原工作流作为参考保存在 `docs/original-pages-workflow.yml`。

开发校验需要 Python 3 与 Node.js（用于 JavaScript 语法检查）。

```sh
python3 scripts/validate.py
```

推送 `main` 后，GitHub Actions 校验资源并将 **仅 `site/`** 部署到 GitHub Pages。首次部署需将仓库 Settings → Pages → Source 设置为 **GitHub Actions**。工作流失败时不会发布新的页面。

## 重新提取 CAD

日常使用不需要原始 SolidWorks 或额外依赖。重新提取时，在独立虚拟环境安装 `sldkit==0.2.0`、`numpy`，从上游下载上述固定附件，保留正确的中文文件名和子装配引用：

```sh
python scripts/build_model.py /path/to/SolidWorks-FT2026-09-25
python scripts/validate.py
```

原始 343 MB CAD 压缩包不纳入仓库。`site/model/model.json` 记录各零件哈希；`audit.json` 保留源变换与舵机编号链。坐标单位 mm，+X 向前、+Y 向机器人左、+Z 向上。提取脚本不修改源 CAD。

## 许可与贡献

- 本工具的原创网页代码和提取/校验脚本：MIT，见 [LICENSE](LICENSE)。
- 上游 CAD 及本项目衍生的 `site/model/` 显示网格、装配数据：**CC BY-NC-SA 4.0**，署名机械行者Robo、fanhao375、Pollen Robotics；保留非商业性使用与相同方式共享条件。见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
- Three.js / OrbitControls：MIT，保留其独立许可。

报告问题时请附模型版本、ID、观察方向和截图；变更编号应说明源装配依据。不要将不同结构版本的标注直接套用到当前模型。

English: An independent, browser-based 3D servo ID and assembly guide for the Feetech HD-1910 MicroDuck replica. Chinese UI; no hardware control. CAD-derived assets are CC BY-NC-SA 4.0 and must not be treated as MIT-licensed software.
