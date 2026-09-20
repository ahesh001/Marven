# Inject Neural Memory
neural_memory_path = base / "marven_neural_memory.json"
neural_memory = json.loads(neural_memory_path.read_text()) if neural_memory_path.exists() else {}

for key, value in neural_memory.get("personality_traits", {}).items():
    system_messages.append(("system", f"trait_{key}: {value}"))

for mem in neural_memory.get("emotional_memory", []):
    system_messages.append(("system", f"memory_{mem['key']}: {mem['value']}"))

for loop in neural_memory.get("philosophical_loops", []):
    system_messages.append(("system", f"thought_loop: {loop}"))