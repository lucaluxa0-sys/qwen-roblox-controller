# SP11 Exact Autonomous Task — Async / Timing (S139-S150)

Use only after SP10 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP11_ScriptingTests

Controller 6.3.27+ required. Preserve the harness after verification. Never create a new benchmark run.

## Required flow
1. script_read exact target. If missing in Edit mode, use the controller-approved normal-Script bootstrap path. Before creation, supervisor_decision_trace must include intended_script_class="Script".
2. supervisor_decision_trace before the real source mutation.
3. Install the exact harness below transactionally and authoritative script_read.
4. supervisor_decision_trace before Play, start Play once, get_console_output after completion.
5. Require [SP11] S139 PASS through S150 PASS and [SP11] COMPLETE, with no relevant runtime error.
6. Stop Play.
7. supervisor_decision_trace then supervisor_benchmark_record on the exact existing run_id, results S139-S150 PASS, pack_complete=["SP11"], no batch_complete.
8. Require controller-verified 150 PASS, 0 partial, 0 fail, SP01-SP11 complete, gate clear.
9. Preserve harness and emit [TASK_COMPLETE].

## Exact harness

```lua
local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP11] " .. id .. " PASS")
	else
		warn("[SP11] " .. id .. " FAIL: " .. tostring(err))
	end
end

runTest("S139", function()
	local started = os.clock()
	local dt = task.wait()
	local elapsed = os.clock() - started
	assert(type(dt) == "number")
	assert(elapsed >= 0)
end)

runTest("S140", function()
	local owner = Instance.new("Folder")
	owner.Parent = script
	local acted = false
	task.delay(0.03, function()
		if owner.Parent ~= nil then
			acted = true
		end
	end)
	owner:Destroy()
	task.wait(0.06)
	assert(acted == false)
end)

runTest("S141", function()
	local finished = Instance.new("BindableEvent")
	local surfaced
	task.spawn(function()
		local ok, err = xpcall(function()
			error("intentional SP11 spawn error")
		end, debug.traceback)
		surfaced = (not ok) and type(err) == "string" and string.find(err, "intentional SP11 spawn error", 1, true) ~= nil
		finished:Fire()
	end)
	finished.Event:Wait()
	assert(surfaced == true)
	finished:Destroy()
end)

runTest("S142", function()
	local owner = Instance.new("Folder")
	owner.Parent = script
	local generation = 1
	local applied = false
	local myGeneration = generation
	task.delay(0.03, function()
		if owner.Parent ~= nil and generation == myGeneration then
			applied = true
		end
	end)
	generation += 1
	owner:Destroy()
	task.wait(0.06)
	assert(applied == false)
end)

runTest("S143", function()
	local timeout = Instance.new("BindableEvent")
	local timedOut = false
	task.delay(0.03, function()
		timedOut = true
		timeout:Fire()
	end)
	timeout.Event:Wait()
	assert(timedOut == true)
	timeout:Destroy()
end)

runTest("S144", function()
	local finished = Instance.new("BindableEvent")
	local results = {}
	local remaining = 2
	local function done(name, value)
		results[name] = value
		remaining -= 1
		if remaining == 0 then
			finished:Fire()
		end
	end
	task.spawn(function()
		done("A", 72)
	end)
	task.spawn(function()
		done("B", 72)
	end)
	if remaining > 0 then
		finished.Event:Wait()
	end
	assert(results.A + results.B == 144)
	finished:Destroy()
end)

runTest("S145", function()
	local good = coroutine.create(function(value)
		return value + 1
	end)
	local ok, value = coroutine.resume(good, 144)
	assert(ok == true and value == 145)
	local bad = coroutine.create(function()
		error("intentional coroutine failure")
	end)
	local badOk, badErr = coroutine.resume(bad)
	assert(badOk == false)
	assert(type(badErr) == "string")
end)

runTest("S146", function()
	local spawned = Instance.new("BindableEvent")
	local backgroundResult
	task.spawn(function()
		backgroundResult = 146
		spawned:Fire()
	end)
	spawned.Event:Wait()
	assert(backgroundResult == 146)
	spawned:Destroy()

	local controlled = coroutine.create(function()
		coroutine.yield("paused")
		return "done"
	end)
	local ok1, state1 = coroutine.resume(controlled)
	assert(ok1 and state1 == "paused")
	local ok2, state2 = coroutine.resume(controlled)
	assert(ok2 and state2 == "done")
end)

runTest("S147", function()
	local event = Instance.new("BindableEvent")
	local shared = 0
	local handled = 0
	local done = Instance.new("BindableEvent")
	local connection = event.Event:Connect(function(delta)
		-- No yield in the critical update: each callback commits one complete mutation.
		local nextValue = shared + delta
		shared = nextValue
		handled += 1
		if handled == 2 then
			done:Fire()
		end
	end)
	task.spawn(function() event:Fire(70) end)
	task.spawn(function() event:Fire(77) end)
	done.Event:Wait()
	assert(shared == 147)
	connection:Disconnect()
	event:Destroy()
	done:Destroy()
end)

runTest("S148", function()
	local generation = 1
	local applied = {}
	local function schedule(value)
		local token = generation
		task.delay(0.02, function()
			if token == generation then
				table.insert(applied, value)
			end
		end)
	end
	schedule("stale")
	generation += 1
	schedule("current")
	task.wait(0.06)
	assert(#applied == 1 and applied[1] == "current")
end)

runTest("S149", function()
	local started = os.clock()
	task.wait(0.02)
	local elapsed = os.clock() - started
	assert(elapsed >= 0.01)
	assert(elapsed < 1)
end)

runTest("S150", function()
	local currentGeneration = 1
	local oldObject = Instance.new("Folder")
	oldObject.Parent = script
	local newObject
	local oldApplied = false
	local newApplied = false

	local oldToken = currentGeneration
	task.delay(0.03, function()
		if oldToken == currentGeneration and oldObject.Parent ~= nil then
			oldApplied = true
		end
	end)

	currentGeneration += 1
	oldObject:Destroy()
	newObject = Instance.new("Folder")
	newObject.Parent = script
	local newToken = currentGeneration
	task.delay(0.03, function()
		if newToken == currentGeneration and newObject.Parent ~= nil then
			newApplied = true
		end
	end)

	task.wait(0.07)
	assert(oldApplied == false)
	assert(newApplied == true)
	newObject:Destroy()
end)

print("[SP11] COMPLETE")
```
