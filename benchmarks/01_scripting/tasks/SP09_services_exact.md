# SP09 Exact Autonomous Task — Roblox Services / API (S109-S122)

Use only after SP08 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP09_ScriptingTests

Controller 6.3.26+ required. Preserve the harness after verification. Never create a new benchmark run.

## Required flow
1. script_read exact target. If missing in Edit mode, call supervisor_decision_trace with intended_script_class="Script", then follow the controller's exact normal-Script bootstrap multi_edit path and reread.
2. supervisor_decision_trace before the real source mutation.
3. Install the exact harness below transactionally and authoritative script_read.
4. supervisor_decision_trace, start Play once, get_console_output after completion.
5. Require [SP09] S109 PASS through S122 PASS and [SP09] COMPLETE, no relevant runtime error.
6. Stop Play.
7. supervisor_decision_trace, then supervisor_benchmark_record on the exact existing run_id, results S109-S122 PASS, pack_complete=["SP09"], no batch_complete.
8. Require controller-verified aggregate 122 PASS, 0 partial, 0 fail, SP01-SP09 complete, gate clear.
9. Preserve harness and emit [TASK_COMPLETE].

## Exact harness

```lua
local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP09] " .. id .. " PASS")
	else
		warn("[SP09] " .. id .. " FAIL: " .. tostring(err))
	end
end

runTest("S109", function()
	local Players = game:GetService("Players")
	assert(Players:IsA("Players"))
end)

runTest("S110", function()
	local Players = game:GetService("Players")
	local characterConnections = {}
	local function bindPlayer(player)
		local connection = player.CharacterAdded:Connect(function(character)
			assert(character:IsA("Model"))
		end)
		table.insert(characterConnections, connection)
		if player.Character then
			assert(player.Character:IsA("Model"))
		end
	end
	local playerAdded = Players.PlayerAdded:Connect(bindPlayer)
	for _, player in Players:GetPlayers() do
		bindPlayer(player)
	end
	assert(playerAdded.Connected == true)
	playerAdded:Disconnect()
	for _, connection in characterConnections do
		connection:Disconnect()
	end
end)

runTest("S111", function()
	local RunService = game:GetService("RunService")
	assert(RunService:IsServer() == true)
	local dt = RunService.Heartbeat:Wait()
	assert(type(dt) == "number" and dt >= 0)
end)

runTest("S112", function()
	local Debris = game:GetService("Debris")
	local folder = Instance.new("Folder")
	folder.Name = "__QWEN_SP09_DEBRIS__"
	folder.Parent = script
	Debris:AddItem(folder, 0.05)
	task.wait(0.12)
	assert(folder.Parent == nil)
end)

runTest("S113", function()
	local CollectionService = game:GetService("CollectionService")
	local folder = Instance.new("Folder")
	folder.Name = "__QWEN_SP09_TAGGED__"
	folder.Parent = script
	local tag = "__QWEN_SP09_TAG__"
	CollectionService:AddTag(folder, tag)
	assert(CollectionService:HasTag(folder, tag))
	local found = false
	for _, instance in CollectionService:GetTagged(tag) do
		if instance == folder then
			found = true
			break
		end
	end
	assert(found)
	CollectionService:RemoveTag(folder, tag)
	assert(not CollectionService:HasTag(folder, tag))
	folder:Destroy()
end)

runTest("S114", function()
	local HttpService = game:GetService("HttpService")
	local encoded = HttpService:JSONEncode({name = "SP09", value = 114, enabled = true})
	local decoded = HttpService:JSONDecode(encoded)
	assert(decoded.name == "SP09")
	assert(decoded.value == 114)
	assert(decoded.enabled == true)
end)

runTest("S115", function()
	local TweenService = game:GetService("TweenService")
	local value = Instance.new("NumberValue")
	value.Value = 0
	local tween = TweenService:Create(value, TweenInfo.new(0.05), {Value = 115})
	tween:Play()
	tween.Completed:Wait()
	assert(math.abs(value.Value - 115) < 0.001)
	value:Destroy()
end)

runTest("S116", function()
	local PhysicsService = game:GetService("PhysicsService")
	local group = "__QWEN_SP09_GROUP__"
	if PhysicsService:IsCollisionGroupRegistered(group) then
		PhysicsService:UnregisterCollisionGroup(group)
	end
	PhysicsService:RegisterCollisionGroup(group)
	PhysicsService:CollisionGroupSetCollidable(group, "Default", false)
	local part = Instance.new("Part")
	part.CollisionGroup = group
	assert(part.CollisionGroup == group)
	part:Destroy()
	PhysicsService:UnregisterCollisionGroup(group)
end)

runTest("S117", function()
	local MarketplaceService = game:GetService("MarketplaceService")
	assert(MarketplaceService:IsA("MarketplaceService"))
	assert(typeof(MarketplaceService.UserOwnsGamePassAsync) == "function")
	-- Intentionally do not perform purchases or mutation-like marketplace actions.
end)

runTest("S118", function()
	local ReplicatedStorage = game:GetService("ReplicatedStorage")
	local root = ReplicatedStorage:FindFirstChild("__QWEN_SCRIPT_BENCH__")
	local madeRoot = false
	if not root then
		root = Instance.new("Folder")
		root.Name = "__QWEN_SCRIPT_BENCH__"
		root.Parent = ReplicatedStorage
		madeRoot = true
	end
	local shared = Instance.new("Folder")
	shared.Name = "__SP09_SHARED__"
	shared.Parent = root
	assert(shared:IsDescendantOf(ReplicatedStorage))
	shared:Destroy()
	if madeRoot and #root:GetChildren() == 0 then
		root:Destroy()
	end
end)

runTest("S119", function()
	local ServerStorage = game:GetService("ServerStorage")
	local serverOnly = Instance.new("Folder")
	serverOnly.Name = "__QWEN_SP09_SERVER_ONLY__"
	serverOnly.Parent = ServerStorage
	assert(serverOnly:IsDescendantOf(ServerStorage))
	assert(not serverOnly:IsDescendantOf(game:GetService("ReplicatedStorage")))
	serverOnly:Destroy()
end)

runTest("S120", function()
	local folder = Instance.new("Folder")
	folder:SetAttribute("Count", 120)
	folder:SetAttribute("Label", "SP09")
	folder:SetAttribute("Enabled", true)
	assert(folder:GetAttribute("Count") == 120)
	assert(typeof(folder:GetAttribute("Count")) == "number")
	assert(typeof(folder:GetAttribute("Label")) == "string")
	assert(typeof(folder:GetAttribute("Enabled")) == "boolean")
	folder:Destroy()
end)

runTest("S121", function()
	local container = Instance.new("Folder")
	container.Name = "__QWEN_SP09_TIMING__"
	container.Parent = script
	local existing = Instance.new("Folder")
	existing.Name = "Existing"
	existing.Parent = container
	assert(container.Existing == existing)
	assert(container:FindFirstChild("Missing") == nil)
	task.delay(0.03, function()
		local delayed = Instance.new("Folder")
		delayed.Name = "Delayed"
		delayed.Parent = container
	end)
	local delayed = container:WaitForChild("Delayed", 1)
	assert(delayed ~= nil)
	container:Destroy()
end)

runTest("S122", function()
	local wrongOk = pcall(function()
		game:GetService("__DefinitelyNotARobloxService__")
	end)
	assert(wrongOk == false)
	local Players = game:GetService("Players")
	assert(Players:IsA("Players"))
end)

print("[SP09] COMPLETE")
```
