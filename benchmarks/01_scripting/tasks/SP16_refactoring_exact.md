# SP16 Exact Autonomous Task — Refactoring (S205-S218)

Use only after SP15 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP16_ScriptingTests
Controller 6.3.29+ required.

## Required flow
Use the standard class-declared Script bootstrap flow, install/reread the exact harness, Play once, require S205-S218 PASS + COMPLETE and no relevant runtime error, stop Play, then record exactly S205-S218 PASS with pack_complete=["SP16"], same run_id, no batch_complete. Require aggregate 218 PASS, 0 partial/fail, SP01-SP16 complete, gate clear. Preserve harness and emit [TASK_COMPLETE].

## Exact harness

```lua
local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP16] " .. id .. " PASS")
	else
		warn("[SP16] " .. id .. " FAIL: " .. tostring(err))
	end
end

local function legacyScore(a, b)
	local x = a * 2
	local y = b * 3
	return x + y
end

local function calculateWeightedScore(baseScore, bonusScore)
	local weightedBase = baseScore * 2
	local weightedBonus = bonusScore * 3
	return weightedBase + weightedBonus
end

local function clamp(value, minimum, maximum)
	if value < minimum then
		return minimum
	end
	if value > maximum then
		return maximum
	end
	return value
end

local function duplicatedBeforeA(value)
	return math.floor(clamp(value, 0, 100) + 0.5)
end

local function duplicatedBeforeB(value)
	return math.floor(clamp(value, 0, 100) + 0.5)
end

local function roundedPercent(value)
	return math.floor(clamp(value, 0, 100) + 0.5)
end

local function afterA(value)
	return roundedPercent(value)
end

local function afterB(value)
	return roundedPercent(value)
end

local function longBefore(user)
	if user then
		if user.enabled then
			if typeof(user.score) == "number" then
				return clamp(user.score, 0, 100)
			end
		end
	end
	return 0
end

local function validUser(user)
	return user ~= nil and user.enabled == true and typeof(user.score) == "number"
end

local function normalizedUserScore(user)
	if not validUser(user) then
		return 0
	end
	return clamp(user.score, 0, 100)
end

local MAX_SCORE = 100
local DEFAULT_RETRIES = 3
local MODE_ACTIVE = "active"

local function nestedBefore(user)
	if user then
		if user.enabled then
			return user.score
		end
	end
	return 0
end

local function guardedAfter(user)
	if not user then
		return 0
	end
	if not user.enabled then
		return 0
	end
	return user.score
end

runTest("S205", function()
	for a = 0, 5 do
		for b = 0, 5 do
			assert(legacyScore(a, b) == calculateWeightedScore(a, b))
		end
	end
end)

runTest("S206", function()
	for _, value in {-5, 0, 42.4, 100, 120} do
		assert(duplicatedBeforeA(value) == afterA(value))
		assert(duplicatedBeforeB(value) == afterB(value))
	end
	assert(afterA(42.4) == afterB(42.4))
end)

runTest("S207", function()
	local samples = {
		{enabled = true, score = 50},
		{enabled = false, score = 50},
		{enabled = true, score = 150},
		nil,
	}
	for _, sample in samples do
		assert(longBefore(sample) == normalizedUserScore(sample))
	end
end)

runTest("S208", function()
	assert(MAX_SCORE == 100)
	assert(DEFAULT_RETRIES == 3)
	assert(MODE_ACTIVE == "active")
	assert(clamp(150, 0, MAX_SCORE) == 100)
end)

runTest("S209", function()
	local samples = {
		{enabled = true, score = 9},
		{enabled = false, score = 9},
		nil,
	}
	for _, sample in samples do
		assert(nestedBefore(sample) == guardedAfter(sample))
	end
end)

runTest("S210", function()
	local folder = Instance.new("Folder")
	local tracked = {}
	local scanCount = 0
	local function initialScan()
		scanCount += 1
		for _, child in folder:GetChildren() do
			tracked[child] = true
		end
	end
	initialScan()
	local connection = folder.ChildAdded:Connect(function(child)
		tracked[child] = true
	end)
	local child = Instance.new("Folder")
	child.Parent = folder
	task.wait()
	assert(tracked[child] == true)
	assert(scanCount == 1)
	connection:Disconnect()
	folder:Destroy()
end)

runTest("S211", function()
	local event = Instance.new("BindableEvent")
	local owner = {connections = {}}
	function owner:Add(connection)
		table.insert(self.connections, connection)
	end
	function owner:Destroy()
		for _, connection in self.connections do
			connection:Disconnect()
		end
		table.clear(self.connections)
	end
	local count = 0
	owner:Add(event.Event:Connect(function()
		count += 1
	end))
	event:Fire()
	task.wait()
	assert(count == 1)
	owner:Destroy()
	event:Fire()
	task.wait()
	assert(count == 1)
	event:Destroy()
end)

runTest("S212", function()
	local CONFIG = table.freeze({
		multiplier = 2,
		offset = 12,
	})
	local Behavior = {}
	function Behavior.compute(value)
		return value * CONFIG.multiplier + CONFIG.offset
	end
	assert(Behavior.compute(100) == 212)
end)

runTest("S213", function()
	local Module = {}
	function Module.compute(value)
		return value + 213
	end
	local callerA = function()
		return Module.compute(0)
	end
	local callerB = function()
		return Module.compute(1) - 1
	end
	assert(callerA() == 213)
	assert(callerB() == 213)
end)

runTest("S214", function()
	local requireSideEffects = 0
	local Module = {}
	function Module.new()
		requireSideEffects += 1
		return {started = true}
	end
	assert(requireSideEffects == 0)
	local object = Module.new()
	assert(object.started == true)
	assert(requireSideEffects == 1)
end)

runTest("S215", function()
	local function requireNumber(value)
		if typeof(value) ~= "number" then
			error("[SP16:S215] expected number, got " .. typeof(value), 2)
		end
		return value
	end
	local ok, err = pcall(function()
		requireNumber("wrong")
	end)
	assert(ok == false)
	assert(string.find(tostring(err), "[SP16:S215]", 1, true) ~= nil)
	assert(string.find(tostring(err), "string", 1, true) ~= nil)
end)

runTest("S216", function()
	local function boundary(value: number): number
		return value + 216
	end
	assert(boundary(0) == 216)
end)

runTest("S217", function()
	for a = -3, 8 do
		for b = -3, 8 do
			local before = legacyScore(a, b)
			local after = calculateWeightedScore(a, b)
			assert(before == after)
		end
	end
end)

runTest("S218", function()
	local defect = "one duplicated clamp expression"
	local proposedSmallChange = "extract one helper"
	local proposedLargeChange = "rewrite unrelated subsystem"
	local chosen = (#defect < #proposedLargeChange) and proposedSmallChange or proposedLargeChange
	assert(chosen == "extract one helper")
end)

print("[SP16] COMPLETE")
```
