"""Real-hardware arm drivers.

Each driver implements the same surface as ``sim.mock_arm.MockArm``
(``apply_command`` / ``step`` / ``read_state`` / ``hold`` / ``estop``) so the
``FollowerController`` is agnostic to whether it drives a sim or real robot.

Drivers here may depend on vendored hardware SDKs (e.g. the SO-101 Feetech bus),
so they are imported lazily — importing this subpackage must not pull in any
hardware dependency unless a specific driver is actually constructed.
"""
