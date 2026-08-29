# SP08 Exact Autonomous Task — Events / Connections (S095-S108)

Use only after SP07 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP08_ScriptingTests

Controller 6.3.26+ required. Never create a new benchmark run. Preserve the harness after verification.

## Required flow
1. Narrow script_read of the exact target. If missing in Edit mode, follow the controller's exact normal-Script bootstrap creation path:
   - supervisor_decision_trace
   - one multi_edit with old_string="" and new_string="-- QWEN_CONTROLLER_SCRIPT_BOOTSTRAP"
   - script_read same path.
2. Before replacing bootstrap/current source, call supervisor_decision_trace.
3. Install the exact harness below transactionally, then authoritative script_read.
4. Call supervisor_decision_trace before Play, start Play once, get_console_output once after the harness finishes.
5. Require [SP08] S095 PASS through S108 PASS and [SP08] COMPLETE with no relevant runtime error.
6. Stop Play.
7. Call supervisor_decision_trace, then supervisor_benchmark_record with the exact run_id above, S095-S108 PASS, pack_complete=["SP08"], no batch_complete.
8. Require controller-verified aggregate pass count 108, partial=0, fail=0, SP01-SP08 complete, gate clear.
9. Preserve this script and emit [TASK_COMPLETE].

## Exact harness

```lua
local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP08] " .. id .. " PASS")
	else
		warn("[SP08] " .. id .. " FAIL: " .. tostring(err))
	end
end

runTest("S095", function()
	local event = Instance.new("BindableEvent")
	local gotA, gotB
	local connection = event.Event:Connect(function(a, b)
		gotA, gotB = a, b
	end)
	event:Fire(95, "ok")
	task.wait()
	assert(gotA == 95 and gotB == "ok")
	connection:Disconnect()
	event:Destroy()
end)

runTest("S096", function()
	local owner = Instance.new("Folder")
	local event = Instance.new("BindableEvent")
	event.Parent = owner
	local connection = event.Event:Connect(function() end)
	owner.Destroying:Connect(function()
		connection:Disconnect()
	end)
	owner:Destroy()
	assert(connection.Connected == false)
end)

runTest("S097", function()
	local event = Instance.new("BindableEvent")
	local count = 0
	local connection
	local function connectOnce()
		if connection and connection.Connected then
			return
		end
		connection = event.Event:Connect(function()
			count += 1
		end)
	end
	connectOnce()
	connectOnce()
	event:Fire()
	task.wait()
	assert(count == 1)
	connection:Disconnect()
	event:Destroy()
end)

runTest("S098", function()
	local event = Instance.new("BindableEvent")
	local count = 0
	event.Event:Once(function()
		count += 1
	end)
	event:Fire()
	event:Fire()
	task.wait()
	assert(count == 1)
	event:Destroy()
end)

runTest("S099", function()
	local busy = false
	local successfulRuns = 0
	local function guarded(shouldFail)
		if busy then
			return false
		end
		busy = true
		local ok = pcall(function()
			if shouldFail then
				error("intentional SP08 debounce failure")
			end
			successfulRuns += 1
		end)
		busy = false
		return ok
	end
	assert(guarded(true) == false)
	assert(busy == false)
	assert(guarded(false) == true)
	assert(successfulRuns == 1)
end)

runTest("S100", function()
	local a = Instance.new("Folder")
	local b = Instance.new("Folder")
	local busyByObject = {}
	local function begin(object)
		if busyByObject[object] then
			return false
		end
		busyByObject[object] = true
		return true
	end
	local function finish(object)
		busyByObject[object] = nil
	end
	assert(begin(a) == true)
	assert(begin(a) == false)
	assert(begin(b) == true)
	finish(a)
	finish(b)
	a:Destroy()
	b:Destroy()
end)

runTest("S101", function()
	local bus = Instance.new("BindableEvent")
	local received
	local connection = bus.Event:Connect(function(value)
		received = value
	end)
	local function producer(value)
		bus:Fire(value)
	end
	producer("decoupled")
	task.wait()
	assert(received == "decoupled")
	connection:Disconnect()
	bus:Destroy()
end)

runTest("S102", function()
	local event = Instance.new("BindableEvent")
	local count = 0
	local connection
	local function Start()
		if connection then
			connection:Disconnect()
		end
		connection = event.Event:Connect(function()
			count += 1
		end)
	end
	Start()
	Start()
	event:Fire()
	task.wait()
	assert(count == 1)
	connection:Disconnect()
	event:Destroy()
end)

runTest("S103", function()
	local folder = Instance.new("Folder")
	local existing = Instance.new("Folder")
	existing.Name = "Existing"
	existing.Parent = folder
	local seen = {}
	local function process(child)
		seen[child.Name] = true
	end
	for _, child in folder:GetChildren() do
		process(child)
	end
	local connection = folder.ChildAdded:Connect(process)
	local added = Instance.new("Folder")
	added.Name = "AddedLater"
	added.Parent = folder
	task.wait()
	assert(seen.Existing == true)
	assert(seen.AddedLater == true)
	connection:Disconnect()
	folder:Destroy()
end)

runTest("S104", function()
	local value = Instance.new("NumberValue")
	value.Value = 1
	local fired = 0
	local connection = value:GetPropertyChangedSignal("Value"):Connect(function()
		fired += 1
	end)
	value.Value = 2
	task.wait()
	assert(fired >= 1)
	connection:Disconnect()
	value:Destroy()
end)

runTest("S105", function()
	local event = Instance.new("BindableEvent")
	local target = Instance.new("Folder")
	local usedDestroyedReference = false
	local connection = event.Event:Connect(function()
		if target.Parent ~= nil then
			usedDestroyedReference = true
		end
	end)
	target:Destroy()
	event:Fire()
	task.wait()
	assert(usedDestroyedReference == false)
	connection:Disconnect()
	event:Destroy()
end)

runTest("S106", function()
	local event = Instance.new("BindableEvent")
	local handled = false
	local count = 0
	local connection
	connection = event.Event:Connect(function()
		if handled then
			return
		end
		handled = true
		count += 1
		event:Fire()
	end)
	event:Fire()
	task.wait()
	assert(count == 1)
	connection:Disconnect()
	event:Destroy()
end)

runTest("S107", function()
	local Owner = {}
	Owner.__index = Owner
	function Owner.new()
		return setmetatable({connections = {}}, Owner)
	end
	function Owner:Add(connection)
		table.insert(self.connections, connection)
	end
	function Owner:Destroy()
		for _, connection in self.connections do
			connection:Disconnect()
		end
		table.clear(self.connections)
	end

	local eventA = Instance.new("BindableEvent")
	local eventB = Instance.new("BindableEvent")
	local owner = Owner.new()
	local count = 0
	owner:Add(eventA.Event:Connect(function() count += 1 end))
	owner:Add(eventB.Event:Connect(function() count += 1 end))
	eventA:Fire()
	eventB:Fire()
	task.wait()
	assert(count == 2)
	owner:Destroy()
	eventA:Fire()
	eventB:Fire()
	task.wait()
	assert(count == 2)
	eventA:Destroy()
	eventB:Destroy()
end)

runTest("S108", function()
	local event = Instance.new("BindableEvent")
	local count = 0
	local function handler()
		count += 1
	end
	local primary = event.Event:Connect(handler)
	local duplicate = event.Event:Connect(handler)
	event:Fire()
	task.wait()
	assert(count == 2)
	duplicate:Disconnect()
	count = 0
	event:Fire()
	task.wait()
	assert(count == 1)
	primary:Disconnect()
	event:Destroy()
end)

print("[SP08] COMPLETE")
```
