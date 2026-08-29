# SP10 Exact Autonomous Task — Instances from Code (S123-S138)

Use only after SP09 is controller-verified complete.

Benchmark-Run-ID: scripting-s001-s024-structured-run6-20260829T0806Z
Target: ServerScriptService.__QWEN_SCRIPT_BENCH__.SP10_ScriptingTests

Controller 6.3.26+ required. Preserve the harness after verification. Never create a new benchmark run.

## Required flow
1. script_read exact target. If missing in Edit mode, use the controller-approved normal-Script bootstrap multi_edit and reread.
2. supervisor_decision_trace before real source mutation.
3. Install the exact harness below transactionally and authoritative script_read.
4. supervisor_decision_trace before Play, start Play once, get_console_output after completion.
5. Require [SP10] S123 PASS through S138 PASS and [SP10] COMPLETE, no relevant runtime error.
6. Stop Play.
7. supervisor_decision_trace then supervisor_benchmark_record on the exact existing run_id, results S123-S138 PASS, pack_complete=["SP10"], no batch_complete.
8. Require controller-verified 138 PASS, 0 partial, 0 fail, SP01-SP10 complete, gate clear.
9. Preserve harness and emit [TASK_COMPLETE].

## Exact harness

```lua
local CollectionService = game:GetService("CollectionService")

local function runTest(id, fn)
	local ok, err = pcall(fn)
	if ok then
		print("[SP10] " .. id .. " PASS")
	else
		warn("[SP10] " .. id .. " FAIL: " .. tostring(err))
	end
end

runTest("S123", function()
	local folder = Instance.new("Folder")
	folder.Name = "__QWEN_SP10_ROOT__"
	local part = Instance.new("Part")
	part.Name = "ConfiguredBeforeParent"
	part.Anchored = true
	part.Size = Vector3.new(2, 3, 4)
	part.Position = Vector3.new(0, 10, 0)
	part.Parent = folder
	folder.Parent = script
	assert(part.Parent == folder)
	assert(part.Anchored == true)
	assert(part.Size == Vector3.new(2, 3, 4))
	folder:Destroy()
end)

runTest("S124", function()
	local original = Instance.new("Folder")
	original.Name = "Original"
	original:SetAttribute("Value", 124)
	local child = Instance.new("StringValue")
	child.Name = "Data"
	child.Value = "original"
	child.Parent = original
	local clone = original:Clone()
	clone.Name = "Clone"
	clone.Data.Value = "clone"
	clone:SetAttribute("Value", 125)
	assert(original.Data.Value == "original")
	assert(clone.Data.Value == "clone")
	assert(original:GetAttribute("Value") == 124)
	assert(clone:GetAttribute("Value") == 125)
	original:Destroy()
	clone:Destroy()
end)

runTest("S125", function()
	local object = Instance.new("Folder")
	object.Parent = script
	local stale = object
	object:Destroy()
	assert(stale.Parent == nil)
	local safeName = stale.Parent and stale.Name or "destroyed"
	assert(safeName == "destroyed")
end)

runTest("S126", function()
	local a = Instance.new("Folder")
	local b = Instance.new("Folder")
	local child = Instance.new("Folder")
	a.Parent = script
	b.Parent = script
	child.Parent = a
	assert(child.Parent == a)
	child.Parent = b
	assert(child.Parent == b)
	a:Destroy()
	b:Destroy()
end)

runTest("S127", function()
	local part = Instance.new("Part")
	part.Anchored = true
	part.CanCollide = false
	part.Transparency = 0.25
	part.Color = Color3.fromRGB(12, 34, 56)
	part.Material = Enum.Material.SmoothPlastic
	part.Size = Vector3.new(1, 2, 3)
	assert(part.Anchored == true)
	assert(part.CanCollide == false)
	assert(part.Transparency == 0.25)
	assert(part.Size == Vector3.new(1, 2, 3))
	part:Destroy()
end)

runTest("S128", function()
	local position = Vector3.new(1, 2, 3)
	local transform = CFrame.new(position) * CFrame.Angles(0, math.rad(90), 0)
	assert(typeof(position) == "Vector3")
	assert(typeof(transform) == "CFrame")
	assert(transform.Position == position)
	local part = Instance.new("Part")
	part.CFrame = transform
	assert(part.Position == position)
	part:Destroy()
end)

runTest("S129", function()
	local model = Instance.new("Model")
	local part = Instance.new("Part")
	part.Anchored = true
	part.Parent = model
	model.Parent = script
	model:PivotTo(CFrame.new(12, 9, -4))
	local pivot = model:GetPivot()
	assert((pivot.Position - Vector3.new(12, 9, -4)).Magnitude < 0.01)
	model:Destroy()
end)

runTest("S130", function()
	local root = Instance.new("Folder")
	local nested = Instance.new("Folder")
	nested.Parent = root
	local partA = Instance.new("Part")
	partA.Name = "Anything"
	partA.Parent = root
	local partB = Instance.new("Part")
	partB.Name = "NoKeywordNeeded"
	partB.Parent = nested
	local found = 0
	for _, descendant in root:GetDescendants() do
		if descendant:IsA("BasePart") then
			found += 1
		end
	end
	assert(found == 2)
	root:Destroy()
end)

runTest("S131", function()
	local object = Instance.new("Folder")
	object.Parent = script
	object:SetAttribute("BenchmarkKind", "Target")
	local tag = "__QWEN_SP10_TARGET__"
	CollectionService:AddTag(object, tag)
	assert(object:GetAttribute("BenchmarkKind") == "Target")
	assert(CollectionService:HasTag(object, tag))
	CollectionService:RemoveTag(object, tag)
	object:Destroy()
end)

runTest("S132", function()
	local folder = Instance.new("Folder")
	local missing = folder:FindFirstChild("Score")
	assert(missing == nil)
	local value
	if missing and missing:IsA("NumberValue") then
		value = missing.Value
	end
	assert(value == nil)
	local score = Instance.new("NumberValue")
	score.Name = "Score"
	score.Value = 132
	score.Parent = folder
	local found = folder:FindFirstChild("Score")
	assert(found and found:IsA("NumberValue"))
	assert(found.Value == 132)
	folder:Destroy()
end)

runTest("S133", function()
	local target = Instance.new("Folder")
	local reference = Instance.new("ObjectValue")
	reference.Value = target
	assert(reference.Value == target)
	target:Destroy()
	assert(reference.Value == nil)
	reference:Destroy()
end)

runTest("S134", function()
	local numberValue = Instance.new("NumberValue")
	local stringValue = Instance.new("StringValue")
	local boolValue = Instance.new("BoolValue")
	numberValue.Value = 134
	stringValue.Value = "SP10"
	boolValue.Value = true
	assert(numberValue.Value == 134)
	assert(stringValue.Value == "SP10")
	assert(boolValue.Value == true)
	numberValue:Destroy()
	stringValue:Destroy()
	boolValue:Destroy()
end)

runTest("S135", function()
	local misleading = Instance.new("Folder")
	misleading.Name = "Part"
	assert(misleading.Name == "Part")
	assert(misleading:IsA("Folder"))
	assert(not misleading:IsA("Part"))
	misleading:Destroy()
end)

runTest("S136", function()
	local accessory = Instance.new("Accessory")
	local handle = Instance.new("Part")
	handle.Name = "Handle"
	handle.Parent = accessory
	assert(accessory:FindFirstChild("Handle") == handle)
	assert(handle:IsA("BasePart"))
	assert(accessory:FindFirstChild("RootPart") == nil)
	accessory:Destroy()
end)

runTest("S137", function()
	local root = Instance.new("Folder")
	root.Name = "__QWEN_SP10_BATCH__"
	root.Parent = script
	local left = Instance.new("Folder")
	left.Name = "Left"
	left.Parent = root
	local right = Instance.new("Folder")
	right.Name = "Right"
	right.Parent = root
	local item = Instance.new("Folder")
	item.Name = "Before"
	item.Parent = left
	item.Name = "After"
	item.Parent = right
	assert(root:FindFirstChild("Right") == right)
	assert(right:FindFirstChild("After") == item)
	assert(left:FindFirstChild("Before") == nil)
	root:Destroy()
end)

runTest("S138", function()
	local unrelated = Instance.new("Folder")
	unrelated.Name = "__QWEN_SP10_UNRELATED_SENTINEL__"
	unrelated.Parent = script
	local created = Instance.new("Folder")
	created.Name = "__QWEN_SP10_CLEANUP_ROOT__"
	created.Parent = script
	for i = 1, 4 do
		local child = Instance.new("Folder")
		child.Name = "Temp" .. i
		child.Parent = created
	end
	created:Destroy()
	assert(script:FindFirstChild("__QWEN_SP10_CLEANUP_ROOT__") == nil)
	assert(unrelated.Parent == script)
	unrelated:Destroy()
end)

print("[SP10] COMPLETE")
```
