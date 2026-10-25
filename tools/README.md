# 工具目录

| 目录 | 功能 | 启动方式 | 是否操作实机 |
|---|---|---|---|
| [calibrator](calibrator/README.md) | 逐关节零位标定、3D 参考、备份与保持 | `cd tools/calibrator && python3 server.py --demo` | 默认模拟；显式使用 SSH 并进入标定后才接入实机 |
| [servo-guide](servo-guide/README.md) | 舵机编号与装配对照 | `python3 -m http.server 8772 --bind 127.0.0.1 --directory tools/servo-guide/site` | 纯查看 |

命令从仓库根目录执行。每个工具有自己的 README、来源与许可说明。工具之间不共享实机连接、标定记录或配置。
