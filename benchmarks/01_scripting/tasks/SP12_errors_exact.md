# SP12 Exact Autonomous Task — Errors / Defensive Coding (S151-S162)

Use only after SP11 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP12_ScriptingTests

Controller 6.3.27+ required. Preserve the harness. Keep the same benchmark run.

## Required flow
Use the standard controller-safe Script bootstrap flow. Before missing-object creation, declare intended_script_class="Script" in supervisor_decision_trace. Install/reread this exact harness, trace before Play, Play once, require S151-S162 PASS + COMPLETE and no relevant runtime error, stop Play, trace, record exactly S151-S162 PASS with pack_complete=["SP12"], no batch_complete. Require aggregate 162 PASS, 0 partial/fail, SP01-SP12 complete, gate clear. Preserve harness and emit [TASK_COMPLETE].

## Exact harness

```lua
local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP12] " .. id .. " PASS")
	else
		warn("[SP12] " .. id .. " FAIL: " .. tostring(err))
	end
end

runTest("S151", function()
	local ok, err = pcall(function()
		error("legitimate contained failure")
	end)
	assert(ok == false)
	assert(type(err) == "string")
	assert(string.find(err, "legitimate contained failure", 1, true) ~= nil)
end)

runTest("S152", function()
	local ok, trace = xpcall(function()
		error("trace-me")
	end, debug.traceback)
	assert(ok == false)
	assert(type(trace) == "string")
	assert(string.find(trace, "trace-me", 1, true) ~= nil)
end)

runTest("S153", function()
	local function programmerLogic(value)
		assert(type(value) == "number", "ordinary programmer bug must surface")
		return value + 1
	end
	local ok, err = pcall(function()
		programmerLogic("wrong")
	end)
	assert(ok == false)
	assert(string.find(tostring(err), "ordinary programmer bug must surface", 1, true) ~= nil)
end)

runTest("S154", function()
	local function setScore(playerName, score)
		if type(playerName) ~= "string" then
			error("setScore playerName must be string; got " .. typeof(playerName), 2)
		end
		if type(score) ~= "number" then
			error("setScore score must be number; got " .. typeof(score), 2)
		end
		return playerName, score
	end
	local ok, err = pcall(function()
		setScore("Player", "bad")
	end)
	assert(ok == false)
	assert(string.find(tostring(err), "setScore score must be number", 1, true) ~= nil)
end)

runTest("S155", function()
	local state = {initialized = true}
	assert(state.initialized == true, "programmer invariant: state must be initialized here")
end)

runTest("S156", function()
	local folder = Instance.new("Folder")
	local optional = folder:FindFirstChild("Optional")
	local value = optional and optional.Name or "default"
	assert(value == "default")
	folder:Destroy()
end)

runTest("S157", function()
	local folder = Instance.new("Folder")
	local ok, err = pcall(function()
		local required = folder:FindFirstChild("Required")
		assert(required ~= nil, "[S157] required benchmark dependency missing")
	end)
	assert(ok == false)
	assert(string.find(tostring(err), "[S157]", 1, true) ~= nil)
	folder:Destroy()
end)

runTest("S158", function()
	local attempts = 0
	local function transientOperation()
		attempts += 1
		if attempts < 3 then
			return false, "transient"
		end
		return true, "ok"
	end
	local success, result
	for _ = 1, 3 do
		success, result = transientOperation()
		if success then
			break
		end
	end
	assert(success == true)
	assert(result == "ok")
	assert(attempts == 3)
end)

runTest("S159", function()
	local attempts = 0
	local maxAttempts = 2
	local lastError
	for _ = 1, maxAttempts do
		attempts += 1
		local ok, err = pcall(function()
			error("deterministic")
		end)
		if ok then
			break
		end
		lastError = err
	end
	assert(attempts == maxAttempts)
	assert(string.find(tostring(lastError), "deterministic", 1, true) ~= nil)
end)

runTest("S160", function()
	local id = "S160"
	local message = string.format("[%s] invalid benchmark state: %s", id, "example")
	assert(string.find(message, "[S160]", 1, true) ~= nil)
end)

runTest("S161", function()
	local function classify(record)
		if record.layer == "controller" or record.layer == "mcp_transport" then
			return "transport"
		end
		if record.layer == "roblox_runtime" or record.layer == "roblox_api" then
			return "runtime_api"
		end
		return "unknown"
	end
	assert(classify({layer = "roblox_api", message = "API rejected input"}) == "runtime_api")
	assert(classify({layer = "roblox_runtime", message = "Luau error"}) == "runtime_api")
	assert(classify({layer = "mcp_transport", message = "connection closed"}) == "transport")
	assert(classify({layer = "controller", message = "policy block"}) == "transport")
end)

runTest("S162", function()
	local state = {ready = false, value = nil}
	local function load()
		local ok, result = pcall(function()
			error("load failed")
		end)
		if not ok then
			state.ready = false
			state.value = nil
			return false, result
		end
		state.ready = true
		state.value = result
		return true
	end
	local ok, err = load()
	assert(ok == false)
	assert(type(err) == "string")
	assert(state.ready == false)
	assert(state.value == nil)
end)

print("[SP12] COMPLETE")
```
