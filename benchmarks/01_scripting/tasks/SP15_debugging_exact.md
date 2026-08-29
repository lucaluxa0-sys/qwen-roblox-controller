# SP15 Exact Autonomous Task — Debugging (S191-S204)

Use only after SP14 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP15_ScriptingTests
Controller 6.3.29+ required. Preserve the harness.

Use the standard controller-safe Script bootstrap flow with intended_script_class="Script" for missing creation. Before each mutation, Play start, and benchmark record call supervisor_decision_trace. Install and authoritative-reread the exact repaired harness below.

## Exact repaired harness

```lua
local Players = game:GetService("Players")

local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP15] " .. id .. " PASS")
	else
		warn("[SP15] " .. id .. " FAIL: " .. tostring(err))
	end
end

local function deepCopy(value, seen)
	if type(value) ~= "table" then
		return value
	end
	seen = seen or {}
	if seen[value] then
		return seen[value]
	end
	local out = {}
	seen[value] = out
	for key, child in pairs(value) do
		out[deepCopy(key, seen)] = deepCopy(child, seen)
	end
	return out
end

runTest("S191", function()
	-- Repaired form of a prior malformed expression: balanced delimiters and valid Luau.
	local value = (1 + 2) * 3
	assert(value == 9)
end)

runTest("S192", function()
	local root = Instance.new("Folder")
	local child = root:FindFirstChild("Missing")
	local name = child and child.Name or "fallback"
	assert(name == "fallback")
	root:Destroy()
end)

runTest("S193", function()
	local numberValue = Instance.new("NumberValue")
	numberValue.Value = 193
	assert(numberValue.Value == 193)
	assert(numberValue:IsA("NumberValue"))
	numberValue:Destroy()
end)

runTest("S194", function()
	local part = Instance.new("Part")
	assert(part:IsA("BasePart"))
	assert(not part:IsA("Folder"))
	part:Destroy()
end)

runTest("S195", function()
	local part = Instance.new("Part")
	part.Position = Vector3.new(1, 9, 5)
	local position = part.Position
	assert(typeof(position) == "Vector3")
	assert(typeof(part) == "Instance")
	assert(position == Vector3.new(1, 9, 5))
	part:Destroy()
end)

runTest("S196", function()
	local Counter = {}
	Counter.__index = Counter
	function Counter.new()
		return setmetatable({value = 0}, Counter)
	end
	function Counter:add(amount)
		self.value += amount
		return self.value
	end
	local counter = Counter.new()
	assert(counter:add(196) == 196)
end)

runTest("S197", function()
	local helper
	local function caller(value)
		return helper(value)
	end
	helper = function(value)
		return value + 1
	end
	assert(caller(196) == 197)
end)

runTest("S198", function()
	local event = Instance.new("BindableEvent")
	local calls = 0
	local connection = event.Event:Connect(function()
		calls += 1
	end)
	event:Fire()
	assert(calls == 1)
	connection:Disconnect()
	event:Fire()
	task.wait()
	assert(calls == 1)
	assert(connection.Connected == false)
	event:Destroy()
end)

runTest("S199", function()
	local old = Instance.new("Folder")
	old.Name = "Target"
	old.Parent = script
	old:Destroy()
	local fresh = Instance.new("Folder")
	fresh.Name = "Target"
	fresh.Parent = script
	assert(old.Parent == nil)
	assert(fresh.Parent == script)
	assert(old ~= fresh)
	local current = script:FindFirstChild("Target")
	assert(current == fresh)
	fresh:Destroy()
end)

runTest("S200", function()
	assert(Players.LocalPlayer == nil)
	local serverOwned = Instance.new("Folder")
	serverOwned.Name = "__QWEN_SP15_SERVER_OWNED__"
	serverOwned.Parent = script
	assert(serverOwned:IsDescendantOf(game:GetService("ServerScriptService")))
	serverOwned:Destroy()
end)

runTest("S201", function()
	local count = 0
	local maxIterations = 201
	while count < maxIterations do
		count += 1
	end
	assert(count == 201)
end)

runTest("S202", function()
	local original = {
		nested = {
			value = 202,
		},
	}
	local copy = deepCopy(original)
	copy.nested.value = 999
	assert(original.nested.value == 202)
	assert(copy.nested.value == 999)
	assert(original.nested ~= copy.nested)
end)

runTest("S203", function()
	-- Three repaired defects at once: type validation, nil handling, and bounded iteration.
	local function sumNumbers(values, limit)
		if type(values) ~= "table" then
			return 0
		end
		limit = math.max(0, math.floor(tonumber(limit) or #values))
		local total = 0
		for index = 1, math.min(#values, limit) do
			local value = values[index]
			if type(value) == "number" then
				total += value
			end
		end
		return total
	end
	assert(sumNumbers({100, "bad", 103, nil, 999}, 3) == 203)
	assert(sumNumbers(nil, 3) == 0)
end)

runTest("S204", function()
	-- Misleading downstream symptom; prove the root cause at the producer boundary.
	local function producer(config)
		if type(config) ~= "table" or type(config.base) ~= "number" then
			return nil, "producer_config"
		end
		return config.base
	end
	local function middle(config)
		local value, err = producer(config)
		if value == nil then
			return nil, err
		end
		return value + 4
	end
	local function consumer(config)
		local value, err = middle(config)
		if value == nil then
			return nil, err
		end
		return value * 2
	end
	local badValue, rootCause = consumer({base = "wrong"})
	assert(badValue == nil)
	assert(rootCause == "producer_config")
	local fixed = consumer({base = 100})
	assert(fixed == 208)
end)

print("[SP15] COMPLETE")
```

## Runtime / commit flow
1. Fresh trace, start Play once, get_console_output after completion.
2. Require S191-S204 PASS and [SP15] COMPLETE with no relevant runtime error.
3. Stop Play.
4. Fresh trace summarizing exact Output evidence.
5. supervisor_benchmark_record exactly S191-S204 PASS, pack_complete=["SP15"], same run_id, no batch_complete.
6. Require controller-verified 204 PASS, 0 partial, 0 fail, SP01-SP15 complete, gate clear.
7. Preserve harness and emit [TASK_COMPLETE].

Do not resubmit S001-S190. No pathless searches.
