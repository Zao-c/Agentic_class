# IRC5 高风险事件证据包

> 本文件是项目根据 ABB 官方手册自行撰写的事实摘要，不复制手册操作步骤，也不证明学校实机的 RobotWare 版本。

来源为 ABB `Operating manual - Trouble shooting, IRC5`，文档号 `3HAC020738-001` Revision K。已核验的印刷页与事件如下：

- 印刷页 79，`10013 Emergency stop state`：急停装置使 Motors On 回路断开，系统进入急停状态。
- 印刷页 80，`10024 Collision triggered`：机械臂部件与工作站固定设备发生碰撞，运动与程序执行中断。
- 印刷页 80，`10027 Collision retraction fail`：机械臂尝试从碰撞物退回但失败，系统不能恢复正常运行。
- 印刷页 81，`10035 Axis not calibrated`：一个或多个机械臂轴未校准。
- 印刷页 81，`10037 Axis not synchronized`：一个或多个机械臂轴未同步。
- 印刷页 82，`10039 SMB memory is not OK`：串行测量板 SMB 数据与控制器数据可能不一致。
- 印刷页 91，`10420 New unsafe robot path`：目标点修改后形成新的未验证路径，可能存在障碍物和碰撞风险。

课程助教对这些事件只提供只读状态解释、必要信息收集和风险分级。急停复位、碰撞脱困、轴校准/同步、SMB 数据更新和真实机器人路径试跑均由确定性安全策略禁止，并转交教师或授权维护人员。
