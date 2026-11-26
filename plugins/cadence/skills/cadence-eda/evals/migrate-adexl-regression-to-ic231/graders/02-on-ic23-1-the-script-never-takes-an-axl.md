---
type: llm
focus: trace
weight: 1
---

On IC23.1 the script never takes an axl*/adexl run path (axlRunAllTests and the like), and its branch choice does not rely on isCallable of an axl* function, because the axl* session functions still exist in IC23.1
