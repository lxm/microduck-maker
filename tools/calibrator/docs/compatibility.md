# 实机端兼容条件

这份工具从实际使用的 HD1910 维护环境提取，不附带 robotd 固件，也不会自动将任意固件转换为兼容版本。仅查看 3D 和模拟流程不需要板端环境。

## 舵机和系统

- 15 颗 HD1910：10–14、20–24、30–34；型号、ID、单圈 mode 4、EEPROM 锁、温度、电压和断力状态均须通过原有核验。
- 串口 1 Mbps，网页默认 `--serial-port auto`，允许板载 UART、USB/ACM 与稳定设备别名；维护时需要串口独占和 `fuser`。
- Linux、systemd 和 Python 3.10+ 需预装。pyserial 与 `psmisc` 提供的 fuser 缺少时由网页的“检查环境与发现串口”自动准备。首次下载需板端能访问 PyPI/系统软件源；Debian/Ubuntu 上缺少 venv 时补齐 `python3-venv`。
- 电脑用 SSH 密钥登录；板端允许通过 `sudo -n python3` 运行维护代理。运行 `agent.py --check` 的环境同样须能读 robotd 状态。

## robotd 状态与服务

进入前使用 `/run/robotd.sock` 上的 JSON-RPC `robot.busStatus`，检查原生 HD1910 状态、15 个新鲜关节反馈、全部断力、参数恢复完成、策略未运行，以及会话 ID 与周期确实更新。对应字段和检查位于 `agent.py` 的 `native_off_gate()`；不满足时拒绝进入，不修改标志来绕过检查。

兼容旧环境时会优先读取 `/run/lxm-kissqy-hardware.json`。这个文件名是保留的协议约定，不是需要用户手工创建的配置模板。文件存在却过期或错误时，应检查发布它的服务，不能伪造一份状态文件。

当前维护代理认识的 systemd 单元：

```text
codexpad-s10
padd
updaterd
robotd-kissqy-test
robotd
```

必须只有正常 `robotd` 服务工作，不能同时运行 `robotd-kissqy-test`。代理只暂停实际运行的已知服务，获得串口独占后才进入维护；结束时先断力、恢复 RAM 参数，再按原流程恢复服务。未列出的其他串口使用者会由 `fuser` 检查拒绝。

## 路径与配置

环境准备只枚举设备节点、读取运行时端口提示和检查/安装依赖，不打开任何 UART，不停止控制服务。自动模式优先使用 robotd 提示；没有提示时只在唯一候选下自动选择，多候选要求人工选择。设备的不同别名按真实路径合并。进入维护前重新发现，并沿用代理中所选串口与 robotd 串口的一致性检查。

串口描述与 USB 元数据使用 [pySerial 的 list_ports 接口](https://pyserial.readthedocs.io/en/latest/tools.html#serial-tools-list-ports)，并补充枚举板载 UART 与稳定别名节点。

已有可用 pyserial 时使用现有解释器；缺少时使用 `/opt/robot/microduck-calibrator/venv/` 内固定的 `pyserial==3.5`。venv Python 会用于发现和实际维护代理，避免“安装到了一个 Python，运行时却用了另一个”。不使用全局 pip 或 `--break-system-packages`；系统依赖由 apt-get 安装。连接、上传、检查和安装过程实时显示在页面的“SSH 环境检查日志”中，同时写入电脑记录目录的 `setup-stderr.log`。

加 `--no-install-deps` 可关闭自动安装；已有系统/独立环境可以继续使用，缺少依赖则报错并保留原控制服务。非 apt 系统需预装 venv 和 fuser，或自行在板端准备 pyserial；不自动升级 Python 本体。

| 路径 | 用途 |
|---|---|
| `/opt/robot/.calibrator.lock` | 维护互斥 |
| `/opt/robot/.cm4-update.lock` | 与已有更新流程互斥 |
| `/opt/robot/calibration-backups/` | 原寄存器、状态、配置和操作记录 |
| `/etc/robot/hd1910-body-calibration.json` | 原 JSON 模式中可选的身体标定记录 |
| `/etc/robot/hd1910-mouth.json` | 原 JSON 模式中的嘴部端点记录 |

代码还保留一处早期环境的可选锁 `/home/lxm/walk-rehearsal-20261006/stand-tool.lock`；只有其父目录存在时才申请。本次整理保留既有互斥行为，未要求其他用户建立这个个人目录。所有用户仍须核对自己的 robotd/维护程序如何协调串口占用。

Zero 3W 的公开启动示例使用 `--eeprom-only`。此模式不访问上述两个 JSON 标定配置，也不修改 TOML；原始 EEPROM 寄存器和嘴部会话端点仍有日志备份。若运行时对 EEPROM 偏移另有校验或配置缓存，须按该固件的维护方式同步处理，不能假定这个工具会更新任意运行时配置。

## 只读预检

将 `agent.py`、`core.py`、`storage.py`、`board_setup.py` 放到板端同一目录，在该目录执行：

```sh
sudo python3 agent.py --serial-port /dev/ttyS2 --check
```

这项检查读取 robotd 的全断力状态，不停止服务、不打开串口、不发送动作。它只核对已有状态接口，不是对所有后续现场条件的完整验收；失败时按提示检查运行时版本、状态与串口配置。
