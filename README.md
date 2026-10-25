# MicroDuck Maker

**把 MicroDuck 从装配、标定到日常维护会用到的工具，放进一个持续维护的项目。**

[工具目录](tools/README.md) · [标定工具使用说明](tools/calibrator/README.md) · [装配对照](tools/servo-guide/README.md) · [下载发布包](https://github.com/lxm/microduck-maker/releases) · [反馈问题](https://github.com/lxm/microduck-maker/issues)

## 现在可以用什么

| 工具 | 用途 | 入口 |
|---|---|---|
| 逐关节零位标定 | 3D 参考、逐颗采样与保存零位、单独保持、备份与恢复 | [tools/calibrator](tools/calibrator/) |
| 舵机编号与装配对照 | 飞特 HD-1910 版的 15 颗舵机位置、编号与装配检查 | [tools/servo-guide](tools/servo-guide/) · [在线查看](https://lxm.github.io/microduck-servo-guide/) |

当前发布包含这两个工具的源码、第三方资源、使用说明和离线验证程序。`firmware/` 先记录后续固件收录约定，**当前没有可刷写固件**。本仓库不包含标定支架的 STEP/STL 文件。

## 先体验标定工具

电脑安装 Python **3.10+**，然后运行：

```sh
git clone https://github.com/lxm/microduck-maker.git
cd microduck-maker/tools/calibrator
python3 server.py --demo
```

打开 <http://127.0.0.1:8795/>。默认即为模拟模式，可以体验选关节、采样、保存和保持流程，不连接机器人。下载资源均包含在仓库中，不需要 npm 或在线 CDN。

![逐关节标定工具的模拟界面](tools/calibrator/docs/images/demo.png)

实机标定还需要兼容的板端运行环境与 SSH。先读 [适用范围与连接方法](tools/calibrator/README.md#适用范围)，再按 [详细操作说明](tools/calibrator/docs/usage.md) 执行。机械零位和站立 HOME 是不同概念，3D 参考按钮只改变画面。

## 项目结构

```text
microduck-maker/
├── tools/
│   ├── calibrator/       # 标定网页、板端代理、测试与说明
│   └── servo-guide/      # 3D 舵机编号与装配对照
├── firmware/             # 后续固件目录约定，暂不含固件
├── docs/                 # 项目维护与发布说明
├── scripts/              # 全仓库离线验证
├── .github/workflows/    # 自动校验
├── LICENSE              # 原创代码的 MIT 许可
└── THIRD_PARTY_NOTICES.md
```

每个工具在自己的目录内说明依赖、启动方式、兼容范围和来源，能够独立使用。后续新增工具继续放到 `tools/<工具名>/`，固件按板型放到 `firmware/<板型>/`。

## 开发与验证

安装 Python 3.10+ 和 Node.js。Node.js 仅用于 JavaScript 语法校验，不是标定工具的运行依赖。

```sh
python3 scripts/validate.py
```

验证包含标定工具及串口发现/依赖准备的离线测试、3D 引用文件哈希、网页静态资源，以及装配对照的网格、69 个装配变换和 15 个舵机编号。验证不连接机器人、不操作串口、不上传程序或改写实机。

当前版本增加板端串口发现与依赖自动准备，离线测试通过不等同于跨机型实机验收。兼容条件及已知边界见各工具说明。贡献与发布方式见 [CONTRIBUTING.md](CONTRIBUTING.md) 和 [发布说明](docs/releasing.md)。

## 来源与许可

- lxm 原创的标定界面、维护代理、脚本，以及装配对照的原创网页和提取代码采用 [MIT](LICENSE)。
- 标定工具引用的 Kissqy 参考代码保留其**个人非商业使用许可**；其 3D 模型与 Three.js 分别保留上游模型许可、MIT 许可。
- 装配工具的 CAD 衍生模型采用 **CC BY-NC-SA 4.0**，保留机械行者Robo、fanhao375 和 Pollen Robotics 的署名。
- 不同目录的许可范围见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。MIT 不覆盖上述单独授权的资源，不能将整个下载包理解为统一 MIT 授权。

这是独立社区项目，与 Pollen Robotics、飞特及所引用社区项目无官方隶属或背书关系。现有 [microduck-servo-guide](https://github.com/lxm/microduck-servo-guide) 仓库和在线页面继续保留，新项目统一收录后续工具源码。

English: A growing collection of MicroDuck maker tools for assembly, servo zero calibration and maintenance. The calibration UI runs locally with an offline demo; hardware mode requires a compatible HD1910 robot environment. Third-party code and model assets retain their own licenses.
