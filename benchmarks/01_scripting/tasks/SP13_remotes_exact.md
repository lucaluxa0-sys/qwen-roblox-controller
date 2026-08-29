# SP13 Exact Autonomous Task — Client / Server Remotes (S163-S178)

Use only after SP12 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Controller 6.3.29+ required.
Server target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP13_Server
Client target: StarterPlayer.StarterPlayerScripts.__QWEN_SCRIPT_BENCH__.SP13_Client

Never resubmit S001-S162. Preserve both benchmark scripts after verification.

## Required build flow

1. Ensure the Folder `StarterPlayer.StarterPlayerScripts.__QWEN_SCRIPT_BENCH__` exists in Edit mode. If missing, use one fresh supervisor_decision_trace and one narrow non-Script execute_luau that creates only that Folder.
2. script_read the exact client target. If missing, call supervisor_decision_trace with intended_script_class="LocalScript", then create only the inert bootstrap. Prefer create_instances if selectable; otherwise controller 6.3.29 permits the exact prescribed execute_luau fallback for this proven-missing benchmark LocalScript. Reread.
3. Replace only the exact client bootstrap with the exact client source below. Authoritative reread. Satisfy the normal post-edit Play/Output gate. This first client-only verification is allowed to exit silently when SP13 runtime remotes do not exist yet.
4. script_read the exact server target. If missing, call supervisor_decision_trace with intended_script_class="Script", use the normal Script bootstrap multi_edit, then reread.
5. Replace only the exact server bootstrap/current source with the exact server source below. Authoritative reread.
6. Fresh supervisor_decision_trace. Start Play once for final verification. Wait for the server harness to finish, then get_console_output.
7. Require `[SP13] S163 PASS` through `[SP13] S178 PASS` and `[SP13] COMPLETE`, with no relevant runtime error.
8. Stop Play.
9. Fresh supervisor_decision_trace summarizing the actual server/client Output evidence.
10. supervisor_benchmark_record exactly S163-S178 PASS, pack_complete=["SP13"], same run_id, no batch_complete.
11. Require controller-verified aggregate 178 PASS, 0 partial, 0 fail, SP01-SP13 complete, gate clear.
12. Preserve both scripts and emit [TASK_COMPLETE].

No pathless searches. No arbitrary Script.Source through execute_luau. No real purchases, DataStore calls, or player-data mutation.

## Exact client source

```lua
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local player = Players.LocalPlayer
if not player then
	return
end

local runtimeRoot = ReplicatedStorage:WaitForChild("__QWEN_SP13_RUNTIME__", 2)
if not runtimeRoot then
	-- Safe during the preliminary LocalScript-only verification.
	return
end

local remote = runtimeRoot:WaitForChild("Event", 2)
local request = runtimeRoot:WaitForChild("Request", 2)
local allowedRoot = workspace:WaitForChild("__QWEN_SP13_ALLOWED_ROOT__", 2)
local allowedPart = allowedRoot and allowedRoot:WaitForChild("__QWEN_SP13_ALLOWED_PART__", 2)
if not remote or not request or not allowedPart then
	return
end

local notificationSeen = false
local notificationConnection = remote.OnClientEvent:Connect(function(payload)
	if typeof(payload) == "table"
		and payload.action == "Notice"
		and payload.message == "server-owned"
	then
		notificationSeen = true
		remote:FireServer({
			action = "NotifyAck",
			seen = true,
		})
	end
end)

remote:FireServer(164)

remote:FireServer({
	action = "Malformed",
	count = "not-a-number",
})

remote:FireServer({
	action = "Purchase",
	productId = "Potion",
	quantity = 1,
	price = 1,
	damage = 999999,
	claimedBalance = 999999,
})

remote:FireServer({action = "Rate"})
remote:FireServer({action = "Rate"})

remote:FireServer({
	action = "InstanceCheck",
	target = allowedPart,
	expected = "allowed",
})
remote:FireServer({
	action = "InstanceCheck",
	target = workspace.Terrain,
	expected = "rejected",
})

remote:FireServer({
	action = "ClientBoundary",
	storage = "ReplicatedStorage",
})

local response = request:InvokeServer({
	action = "Ping",
	value = 168,
})
remote:FireServer({
	action = "FunctionAck",
	ok = typeof(response) == "table" and response.ok == true and response.value == 168,
})

remote:FireServer({
	action = "ClientReady",
})

local deadline = os.clock() + 3
while not notificationSeen and os.clock() < deadline do
	task.wait()
end

notificationConnection:Disconnect()
```

## Exact server source

```lua
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local ServerStorage = game:GetService("ServerStorage")

local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP13] " .. id .. " PASS")
	else
		warn("[SP13] " .. id .. " FAIL: " .. tostring(err))
	end
end

local oldRuntime = ReplicatedStorage:FindFirstChild("__QWEN_SP13_RUNTIME__")
if oldRuntime then
	oldRuntime:Destroy()
end

local runtimeRoot = Instance.new("Folder")
runtimeRoot.Name = "__QWEN_SP13_RUNTIME__"
runtimeRoot.Parent = ReplicatedStorage

local remote = Instance.new("RemoteEvent")
remote.Name = "Event"
remote.Parent = runtimeRoot

local request = Instance.new("RemoteFunction")
request.Name = "Request"
request.Parent = runtimeRoot

local oldAllowedRoot = workspace:FindFirstChild("__QWEN_SP13_ALLOWED_ROOT__")
if oldAllowedRoot then
	oldAllowedRoot:Destroy()
end

local allowedRoot = Instance.new("Folder")
allowedRoot.Name = "__QWEN_SP13_ALLOWED_ROOT__"
allowedRoot.Parent = workspace

local allowedPart = Instance.new("Part")
allowedPart.Name = "__QWEN_SP13_ALLOWED_PART__"
allowedPart.Anchored = true
allowedPart.CanCollide = false
allowedPart.Transparency = 1
allowedPart.Parent = allowedRoot

local secret = Instance.new("StringValue")
secret.Name = "__QWEN_SP13_SERVER_SECRET__"
secret.Value = "server-only"
secret.Parent = ServerStorage

local SERVER_PRICE = 25
local SERVER_DAMAGE = 7
local state = {
	balance = 100,
	purchases = 0,
}

local observed = {
	primitive = false,
	malformedRejected = false,
	authority = false,
	notification = false,
	functionResult = false,
	rateAccepted = 0,
	rateRejected = 0,
	allowedInstance = false,
	rejectedInstance = false,
	serverAuthoritative = false,
	clientBoundary = false,
	argOrder = false,
	serverPlayer = nil,
	protocolValidated = false,
	transaction = false,
}

local lastRateAt = {}

local function validateProtocol(payload)
	if typeof(payload) ~= "table" then
		return false, "payload_type"
	end
	if typeof(payload.action) ~= "string" then
		return false, "action_type"
	end
	if #payload.action < 1 or #payload.action > 32 then
		return false, "action_length"
	end
	return true
end

local function validatePurchase(payload)
	local ok = validateProtocol(payload)
	if not ok or payload.action ~= "Purchase" then
		return false
	end
	if payload.productId ~= "Potion" then
		return false
	end
	if typeof(payload.quantity) ~= "number"
		or payload.quantity % 1 ~= 0
		or payload.quantity < 1
		or payload.quantity > 3
	then
		return false
	end
	return true
end

local eventConnection
eventConnection = remote.OnServerEvent:Connect(function(player, payload)
	observed.serverPlayer = player
	observed.argOrder = player:IsA("Player")

	if typeof(payload) == "number" then
		observed.primitive = payload == 164
		return
	end

	local schemaOk = validateProtocol(payload)
	if not schemaOk then
		observed.malformedRejected = true
		return
	end

	if payload.action == "Malformed" then
		observed.malformedRejected =
			typeof(payload.count) ~= "number"
		return
	end

	if payload.action == "Purchase" then
		observed.protocolValidated = validatePurchase(payload)
		if not observed.protocolValidated then
			return
		end
		local cost = SERVER_PRICE * payload.quantity
		if state.balance < cost then
			return
		end
		state.balance -= cost
		state.purchases += payload.quantity
		observed.authority =
			payload.price ~= SERVER_PRICE
			and payload.damage ~= SERVER_DAMAGE
		observed.serverAuthoritative =
			state.balance == 75
			and payload.claimedBalance ~= state.balance
		observed.transaction =
			state.purchases == 1
			and state.balance == 75
		return
	end

	if payload.action == "Rate" then
		local now = os.clock()
		local prior = lastRateAt[player]
		if prior and now - prior < 0.25 then
			observed.rateRejected += 1
			return
		end
		lastRateAt[player] = now
		observed.rateAccepted += 1
		return
	end

	if payload.action == "InstanceCheck" then
		if typeof(payload.target) ~= "Instance" then
			return
		end
		local allowed = payload.target:IsDescendantOf(allowedRoot)
		if payload.expected == "allowed" then
			observed.allowedInstance = allowed
		elseif payload.expected == "rejected" then
			observed.rejectedInstance = not allowed
		end
		return
	end

	if payload.action == "ClientBoundary" then
		observed.clientBoundary = payload.storage == "ReplicatedStorage"
		return
	end

	if payload.action == "FunctionAck" then
		observed.functionResult = payload.ok == true
		return
	end

	if payload.action == "NotifyAck" then
		observed.notification = payload.seen == true
		return
	end

	if payload.action == "ClientReady" then
		remote:FireClient(player, {
			action = "Notice",
			message = "server-owned",
		})
		return
	end
end)

request.OnServerInvoke = function(player, payload)
	if not player:IsA("Player") then
		return {ok = false, error = "player"}
	end
	local ok = validateProtocol(payload)
	if not ok or payload.action ~= "Ping" or payload.value ~= 168 then
		return {ok = false, error = "schema"}
	end
	return {
		ok = true,
		value = 168,
	}
end

local function waitUntil(predicate, timeout)
	local deadline = os.clock() + timeout
	repeat
		if predicate() then
			return true
		end
		task.wait()
	until os.clock() >= deadline
	return predicate()
end

local handshakeComplete = waitUntil(function()
	return observed.primitive
		and observed.malformedRejected
		and observed.authority
		and observed.notification
		and observed.functionResult
		and observed.rateAccepted >= 1
		and observed.rateRejected >= 1
		and observed.allowedInstance
		and observed.rejectedInstance
		and observed.serverAuthoritative
		and observed.clientBoundary
		and observed.argOrder
		and observed.protocolValidated
		and observed.transaction
end, 5)

runTest("S163", function()
	assert(remote:IsA("RemoteEvent"))
	assert(eventConnection.Connected)
	assert(observed.serverPlayer and observed.serverPlayer:IsA("Player"))
end)

runTest("S164", function()
	assert(observed.primitive)
end)

runTest("S165", function()
	assert(observed.malformedRejected)
end)

runTest("S166", function()
	assert(observed.authority)
end)

runTest("S167", function()
	assert(observed.notification)
end)

runTest("S168", function()
	assert(request:IsA("RemoteFunction"))
	assert(observed.functionResult)
end)

runTest("S169", function()
	local started = os.clock()
	local bounded = waitUntil(function()
		return false
	end, 0.05)
	assert(bounded == false)
	assert(os.clock() - started < 1)
end)

runTest("S170", function()
	assert(observed.rateAccepted == 1)
	assert(observed.rateRejected >= 1)
end)

runTest("S171", function()
	assert(observed.allowedInstance)
	assert(observed.rejectedInstance)
end)

runTest("S172", function()
	assert(observed.serverAuthoritative)
	assert(state.balance == 75)
end)

runTest("S173", function()
	assert(secret.Parent == ServerStorage)
	assert(ReplicatedStorage:FindFirstChild(secret.Name, true) == nil)
end)

runTest("S174", function()
	assert(observed.argOrder)
end)

runTest("S175", function()
	assert(Players.LocalPlayer == nil)
	assert(observed.serverPlayer and observed.serverPlayer:IsA("Player"))
end)

runTest("S176", function()
	assert(observed.clientBoundary)
end)

runTest("S177", function()
	assert(observed.protocolValidated)
	assert(handshakeComplete)
end)

runTest("S178", function()
	assert(observed.transaction)
	assert(state.purchases == 1)
	assert(state.balance == 75)
end)

print("[SP13] COMPLETE")

eventConnection:Disconnect()
runtimeRoot:Destroy()
allowedRoot:Destroy()
secret:Destroy()
```
