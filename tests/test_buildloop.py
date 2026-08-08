import asyncio

from fastbuild.buildloop import BuildQueue, BuildRequest, TrailingDebounce


async def test_newest_pending_build_wins_while_one_is_running():
    executed = []
    release = asyncio.Event()

    async def execute(request: BuildRequest) -> None:
        executed.append(request.values["w"])
        if len(executed) == 1:
            await release.wait()

    queue = BuildQueue(execute)
    queue.submit(BuildRequest("box", {"w": 1}))
    await asyncio.sleep(0)
    for w in (2, 3, 4):
        queue.submit(BuildRequest("box", {"w": w}))
    release.set()
    await queue.join()
    assert executed == [1, 4]


async def test_drag_storm_coalesces_to_one_fire_per_window_newest_value_wins():
    latest = {"w": 0}
    fired = []
    debounce = TrailingDebounce(0.05, lambda: fired.append(latest["w"]))

    for w in range(20):
        latest["w"] = w
        debounce.touch()
        await asyncio.sleep(0.002)
    await asyncio.sleep(0.08)
    assert fired == [19]


async def test_final_always_fires_immediately():
    latest = {"w": 0}
    fired = []
    debounce = TrailingDebounce(0.05, lambda: fired.append(latest["w"]))

    latest["w"] = 1
    debounce.touch()
    latest["w"] = 2
    debounce.flush()
    assert fired == [2]
    await asyncio.sleep(0.08)
    assert fired == [2]  # pending window was cancelled by flush


async def test_submit_during_build_queues_exactly_one_rebuild():
    executed = []
    release = asyncio.Event()

    async def execute(request: BuildRequest) -> None:
        executed.append(request.values["w"])
        if len(executed) == 1:
            await release.wait()

    queue = BuildQueue(execute)
    queue.submit(BuildRequest("box", {"w": 1}))
    await asyncio.sleep(0)
    queue.submit(BuildRequest("box", {"w": 2}))
    queue.submit(BuildRequest("box", {"w": 2}))
    release.set()
    await queue.join()
    assert executed == [1, 2]
