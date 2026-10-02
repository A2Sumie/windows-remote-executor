# v6/tests/chaos — WRE 混沌矩阵(transport fault injection)

来源:`scratch/ubob-ai-tests/saFUZZ-fuzzing.md` §3(P0)。执行:SA-FUZZ-EXEC(2026-09-15)。
与 `test_failclosed_fuzz.py`(内容畸形)正交互补——这里注入的是**传输/环境故障**,不是字节畸形。

## 注入面(零真机、零改生产码)

- **Seam 1(主)**:`chaos_ssh.py` = PATH shim 假 ssh。`Target.ssh_args` 以字面量
  `"ssh"` 开头走 PATH 解析;conftest 的 `chaos_shim` fixture 把 shim bin 目录前置
  PATH。**`call_request`(subprocess.run)与 `Session`(subprocess.Popen)双链路
  同注**。对端计数器(`requests.log`/`connects.log`)经 `CHAOS_STATE_DIR` 跨进程传递。
- **Seam 2(相位精确)**:沿用 v6/tests/conftest.py 的 `FakePopen`(`popen_patcher`),
  用于写相位必现断裂(E3 恰一次重连)与写相风暴线性界——OS 管道时序在那里
  本质上不确定,用脚本化断裂才确定。

## 确定性契约

- 剧本由 `CHAOS_MODE`/`CHAOS_PARAMS`/`CHAOS_SEED`(固定 20260917)决定;无随机时序。
- 时钟断言只有区间形:`[注入延迟×50%, deadline+margin]`。
- 每个 cell 的外层都有 watchdog(E5 绝不悬挂)。

## 车道落位(gauntlet-testing L3/L5)

- **L3(快,随套件)**:除下面两个文件/格以外的全部格(毫秒~2s)。
- **L5(慢,真实时钟)**:
  - `TestDelay::test_over_deadline_session_realtime`——Popen 链路 31s 真 deadline
    (timeoutMs=1000 → 1s+30s 硬编码 margin)割收 CPU 自旋对端;
  - `TestStderrFlood::test_session_large_flood_must_not_deadlock`——256KB stderr
    洪泛格(修复后快,但保留 timeout(120) 余量)。

## 阴性对照

`test_negative_control.py`:永久 hang shim + 故意错误期望(成功/错误类/不可能时间界/
幻影对端计数)必须被 harness 抓获。红不了 = channel 坏(归因类 4)。

## 本目录抓出并已修的真 bug(2026-09-15)

1. **帧错位**:`Session._round_trip` 不做响应 id 配对——重复/迟到帧会被错当成
   下一次调用的响应。修复:reader 循环跳过"带 id 且 ≠ 在途请求 id"的可证陈旧帧
   (v6 native 每帧回显请求 id,`v6/native/rpc.py` `_build_response`)。
2. **stderr 饿死**:Session 在调用期间无人读 stderr 管道,>64KB 洪泛把对端堵在
   管道写上直到 31s read deadline 收割。修复:`_open` 起 daemon drainer 线程,
   有界 tail(deque 8×4KB)。
修复前均先红(证据:`scratch/ubob-ai-tools/fuzz-p0-report.md`)。
