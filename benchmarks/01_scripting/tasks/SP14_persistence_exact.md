# SP14 Exact Autonomous Task — Persistence / Serialization (S179-S190)

Use only after SP13 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP14_ScriptingTests
Controller 6.3.29+ required.

Never call DataStoreService or mutate real player data. This pack is pure JSON/in-memory persistence design.

## Required flow
1. script_read exact target. If missing in Edit mode, call supervisor_decision_trace with intended_script_class="Script", use only the normal inert Script bootstrap multi_edit, then reread.
2. Fresh supervisor_decision_trace, install the exact harness below transactionally, authoritative reread.
3. Fresh trace, start Play once, get_console_output after completion.
4. Require S179-S190 PASS and [SP14] COMPLETE with no relevant runtime error.
5. Stop Play.
6. Fresh trace, supervisor_benchmark_record exactly S179-S190 PASS, pack_complete=["SP14"], same run_id, no batch_complete.
7. Require aggregate 190 PASS, 0 partial/fail, SP01-SP14 complete, gate clear.
8. Preserve harness, emit [TASK_COMPLETE].

## Exact harness

```lua
local HttpService = game:GetService("HttpService")

local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP14] " .. id .. " PASS")
	else
		warn("[SP14] " .. id .. " FAIL: " .. tostring(err))
	end
end

local DEFAULTS = table.freeze({
	version = 2,
	coins = 0,
	mode = "normal",
	stableId = "",
})

local function copyDefaults()
	return {
		version = DEFAULTS.version,
		coins = DEFAULTS.coins,
		mode = DEFAULTS.mode,
		stableId = DEFAULTS.stableId,
	}
end

local function normalize(raw)
	if typeof(raw) ~= "table" then
		return copyDefaults(), false
	end
	local out = copyDefaults()
	if typeof(raw.version) == "number" then
		out.version = raw.version
	end
	if typeof(raw.coins) == "number" and raw.coins >= 0 and raw.coins <= 100000 then
		out.coins = math.floor(raw.coins)
	end
	if raw.mode == "normal" or raw.mode == "hard" then
		out.mode = raw.mode
	end
	if typeof(raw.stableId) == "string" and #raw.stableId <= 64 then
		out.stableId = raw.stableId
	end
	return out, true
end

local function migrate(raw)
	assert(typeof(raw) == "table")
	if raw.version == 1 then
		return {
			version = 2,
			coins = typeof(raw.gold) == "number" and raw.gold or 0,
			mode = raw.mode or "normal",
			stableId = raw.stableId or "",
		}
	end
	return raw
end

local function serializable(value, seen)
	local kind = typeof(value)
	if kind == "nil" or kind == "boolean" or kind == "number" or kind == "string" then
		return true
	end
	if kind ~= "table" then
		return false
	end
	seen = seen or {}
	if seen[value] then
		return false
	end
	seen[value] = true
	for key, child in pairs(value) do
		if not serializable(key, seen) or not serializable(child, seen) then
			seen[value] = nil
			return false
		end
	end
	seen[value] = nil
	return true
end

local Adapter = {}
Adapter.__index = Adapter

function Adapter.new()
	return setmetatable({store = {}}, Adapter)
end

function Adapter:Load(key)
	local value = self.store[key]
	if value == nil then
		return nil
	end
	return table.clone(value)
end

function Adapter:Save(key, value)
	assert(serializable(value))
	self.store[key] = table.clone(value)
end

function Adapter:Update(key, transform)
	local prior = self:Load(key)
	local nextValue = transform(prior)
	assert(serializable(nextValue))
	self:Save(key, nextValue)
	return self:Load(key)
end

local Domain = {}

function Domain.awardCoins(adapter, key, amount)
	assert(typeof(amount) == "number" and amount >= 0)
	return adapter:Update(key, function(prior)
		local current = prior or copyDefaults()
		current.coins += amount
		return current
	end)
end

runTest("S179", function()
	local original = {
		version = 2,
		coins = 179,
		mode = "hard",
		stableId = "user-179",
	}
	local encoded = HttpService:JSONEncode(original)
	local decoded = HttpService:JSONDecode(encoded)
	assert(decoded.version == 2)
	assert(decoded.coins == 179)
	assert(decoded.mode == "hard")
end)

runTest("S180", function()
	local normalized = normalize({version = 2, coins = 180})
	assert(normalized.coins == 180)
	assert(normalized.mode == "normal")
	assert(normalized.stableId == "")
end)

runTest("S181", function()
	local migrated = migrate({
		version = 1,
		gold = 181,
		mode = "hard",
		stableId = "old-181",
	})
	assert(migrated.version == 2)
	assert(migrated.coins == 181)
	assert(migrated.stableId == "old-181")
end)

runTest("S182", function()
	local folder = Instance.new("Folder")
	assert(serializable({instance = folder}) == false)
	assert(serializable({callback = function() end}) == false)
	folder:Destroy()
end)

runTest("S183", function()
	local runtimeObject = Instance.new("Folder")
	local domainState = {
		coins = 183,
		runtimeObject = runtimeObject,
	}
	local saveData = {
		version = 2,
		coins = domainState.coins,
		mode = "normal",
		stableId = "transient-183",
	}
	assert(serializable(saveData))
	assert(saveData.runtimeObject == nil)
	runtimeObject:Destroy()
end)

runTest("S184", function()
	local adapter = Adapter.new()
	local input = {version = 2, coins = 184, mode = "normal", stableId = "a184"}
	adapter:Save("player", input)
	input.coins = 0
	local loaded = adapter:Load("player")
	assert(loaded.coins == 184)
end)

runTest("S185", function()
	local adapter = Adapter.new()
	adapter:Save("player", {version = 2, coins = 100, mode = "normal", stableId = "a185"})
	local updated = adapter:Update("player", function(prior)
		prior.coins += 85
		return prior
	end)
	assert(updated.coins == 185)
	assert(adapter:Load("player").coins == 185)
end)

runTest("S186", function()
	local attempts = 0
	local function transient()
		attempts += 1
		if attempts < 3 then
			return false, "transient"
		end
		return true, 186
	end
	local ok, value
	for attempt = 1, 3 do
		ok, value = transient()
		if ok then
			break
		end
		task.wait(0.005 * attempt)
	end
	assert(ok == true)
	assert(value == 186)
	assert(attempts == 3)
end)

runTest("S187", function()
	local good = normalize({version = 2, coins = 187, mode = "hard", stableId = "id"})
	assert(good.coins == 187 and good.mode == "hard")
	local bad = normalize({version = 2, coins = -99, mode = "admin", stableId = "id"})
	assert(bad.coins == 0)
	assert(bad.mode == "normal")
end)

runTest("S188", function()
	local stableId = "stable-188-xyz"
	local encoded = HttpService:JSONEncode({
		version = 2,
		coins = 188,
		mode = "normal",
		stableId = stableId,
	})
	local decoded = HttpService:JSONDecode(encoded)
	assert(decoded.stableId == stableId)
end)

runTest("S189", function()
	local ok = pcall(function()
		HttpService:JSONDecode("{ definitely corrupted")
	end)
	assert(ok == false)
	local fallback = copyDefaults()
	assert(fallback.version == 2)
	assert(fallback.coins == 0)
end)

runTest("S190", function()
	local adapter = Adapter.new()
	local result = Domain.awardCoins(adapter, "player", 190)
	assert(result.coins == 190)
	assert(type(adapter.Save) == "function")
	assert(type(Domain.awardCoins) == "function")
end)

print("[SP14] COMPLETE")
```
