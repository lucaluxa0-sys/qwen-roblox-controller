# SP17 Exact Autonomous Task — Performance (S219-S232)

Use only after SP16 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP17_ScriptingTests
Controller 6.3.29+ required.

## Required flow
Use the standard class-declared Script bootstrap flow, install/reread the exact harness, Play once, require S219-S232 PASS + COMPLETE and no relevant runtime error, stop Play, record exactly S219-S232 PASS with pack_complete=["SP17"], same run_id, no batch_complete, and require aggregate 232 PASS / 0 partial / 0 fail / SP01-SP17 / gate clear. Preserve harness and emit [TASK_COMPLETE].

## Exact harness

```lua
local CollectionService = game:GetService("CollectionService")
local RunService = game:GetService("RunService")

local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP17] " .. id .. " PASS")
	else
		warn("[SP17] " .. id .. " FAIL: " .. tostring(err))
	end
end

runTest("S219", function()
	local value = Instance.new("NumberValue")
	local updates = 0
	local connection = value.Changed:Connect(function()
		updates += 1
	end)
	value.Value = 1
	value.Value = 2
	task.wait()
	assert(updates == 2)
	connection:Disconnect()
	value:Destroy()
end)

runTest("S220", function()
	local interval = 0.05
	local lastAt = -math.huge
	local accepted = 0
	local function throttled(now)
		if now - lastAt < interval then
			return false
		end
		lastAt = now
		accepted += 1
		return true
	end
	assert(throttled(0.00) == true)
	assert(throttled(0.01) == false)
	assert(throttled(0.06) == true)
	assert(accepted == 2)
end)

runTest("S221", function()
	local tag = "__QWEN_SP17_TRACK__"
	local tracked = {}
	local root = Instance.new("Folder")
	root.Parent = script
	local added = CollectionService:GetInstanceAddedSignal(tag):Connect(function(instance)
		tracked[instance] = true
	end)
	local removed = CollectionService:GetInstanceRemovedSignal(tag):Connect(function(instance)
		tracked[instance] = nil
	end)
	local part = Instance.new("Part")
	part.Parent = root
	CollectionService:AddTag(part, tag)
	task.wait()
	assert(tracked[part] == true)
	CollectionService:RemoveTag(part, tag)
	task.wait()
	assert(tracked[part] == nil)
	added:Disconnect()
	removed:Disconnect()
	root:Destroy()
end)

runTest("S222", function()
	local ticks = 0
	local connection = RunService.Heartbeat:Connect(function()
		ticks += 1
	end)
	RunService.Heartbeat:Wait()
	assert(connection.Connected)
	connection:Disconnect()
	local snapshot = ticks
	task.wait()
	assert(connection.Connected == false)
	assert(ticks == snapshot)
end)

runTest("S223", function()
	local busy = false
	local spawned = 0
	local completed = Instance.new("BindableEvent")
	local function onStorm()
		if busy then
			return false
		end
		busy = true
		spawned += 1
		task.spawn(function()
			task.wait(0.03)
			busy = false
			completed:Fire()
		end)
		return true
	end
	assert(onStorm() == true)
	for _ = 1, 100 do
		onStorm()
	end
	completed.Event:Wait()
	assert(spawned == 1)
	completed:Destroy()
end)

runTest("S224", function()
	local cache = {}
	local order = {}
	local limit = 10
	local function put(key, value)
		if cache[key] == nil then
			table.insert(order, key)
		end
		cache[key] = value
		while #order > limit do
			local evicted = table.remove(order, 1)
			cache[evicted] = nil
		end
	end
	for i = 1, 100 do
		put(i, i)
	end
	assert(#order == limit)
	local count = 0
	for _ in pairs(cache) do
		count += 1
	end
	assert(count == limit)
end)

runTest("S225", function()
	local cache = {}
	local order = {}
	local limit = 3
	local function put(key)
		if cache[key] == nil then
			table.insert(order, key)
		end
		cache[key] = true
		if #order > limit then
			local oldest = table.remove(order, 1)
			cache[oldest] = nil
		end
	end
	put("A")
	put("B")
	put("C")
	put("D")
	assert(cache.A == nil)
	assert(cache.B and cache.C and cache.D)
end)

runTest("S226", function()
	local parts = {}
	for i = 1, 20 do
		local part = Instance.new("Part")
		part.Name = "P" .. i
		table.insert(parts, part)
	end
	local batches = 0
	for startIndex = 1, #parts, 5 do
		batches += 1
		for index = startIndex, math.min(startIndex + 4, #parts) do
			parts[index].Anchored = true
		end
	end
	assert(batches == 4)
	for _, part in parts do
		assert(part.Anchored)
		part:Destroy()
	end
end)

runTest("S227", function()
	local root = Instance.new("Folder")
	local child = Instance.new("Folder")
	child.Name = "HotDependency"
	child.Parent = root
	local lookups = 0
	local cached
	local function getDependency()
		if not cached then
			lookups += 1
			cached = root:FindFirstChild("HotDependency")
		end
		return cached
	end
	for _ = 1, 100 do
		assert(getDependency() == child)
	end
	assert(lookups == 1)
	root:Destroy()
end)

runTest("S228", function()
	local retained = {}
	local event = Instance.new("BindableEvent")
	local connection = event.Event:Connect(function()
		local object = Instance.new("Folder")
		table.insert(retained, object)
	end)
	event:Fire()
	task.wait()
	assert(#retained == 1)
	connection:Disconnect()
	for _, object in retained do
		object:Destroy()
	end
	table.clear(retained)
	assert(#retained == 0)
	event:Destroy()
end)

runTest("S229", function()
	local event = Instance.new("BindableEvent")
	local listeners = {}
	for i = 1, 20 do
		listeners[i] = 0
	end
	local physicalConnections = 0
	local connection = event.Event:Connect(function()
		for i = 1, #listeners do
			listeners[i] += 1
		end
	end)
	physicalConnections += 1
	event:Fire()
	task.wait()
	assert(physicalConnections == 1)
	for i = 1, #listeners do
		assert(listeners[i] == 1)
	end
	connection:Disconnect()
	event:Destroy()
end)

runTest("S230", function()
	local items = table.create(100)
	for i = 1, 100 do
		items[i] = i
	end
	local beforeOps = 0
	for _ = 1, 10 do
		for _ in items do
			beforeOps += 1
		end
	end
	local afterOps = 0
	local cachedCount = #items
	for _ = 1, 10 do
		afterOps += 1
		assert(cachedCount == 100)
	end
	assert(beforeOps == 1000)
	assert(afterOps == 10)
	assert(afterOps < beforeOps)
end)

runTest("S231", function()
	local measurements = {
		repeatedScanOps = 1000,
		cachedOps = 10,
	}
	local shouldOptimize = measurements.cachedOps < measurements.repeatedScanOps
	assert(shouldOptimize == true)
end)

runTest("S232", function()
	local requiredTotal = 100
	local processed = 0
	local perSlice = 10
	while processed < requiredTotal do
		local sliceEnd = math.min(processed + perSlice, requiredTotal)
		while processed < sliceEnd do
			processed += 1
		end
		task.wait()
	end
	assert(processed == requiredTotal)
end)

print("[SP17] COMPLETE")
```
