# Summary of Changes

## Issues Fixed

### 1. test_detect_geekbench_installed_with_cli
**Problem**: The test was failing because it only mocked one subprocess call (version check), but the actual code makes two calls - version check and help check. The READY_AUTO status is only returned if `--run` appears in the help output.

**Solution**: Updated the test to mock both subprocess calls using a side_effect function that returns different responses based on the command being executed.

### 2. test_executor_initialization
**Problem**: The lazy `__getattr__` in `app/__init__.py` was trying to import `SessionModel` and `ResultModel` from `app.database`, but these classes don't exist (only `Database` exists).

**Solution**: Removed the non-existent imports from the `__getattr__` function.

### 3. Performance Issue
**Problem**: `BenchmarkManager.__init__()` was calling `_detect_system_capabilities()`, which runs real system detection commands (WSL, dxdiag, vulkaninfo, PowerShell), making each test take 9-10 seconds and the total suite ~48 seconds.

**Solution**: 
- Moved discovery out of `__init__` into an explicit `scan()` method
- Updated tests to mock system capabilities detection
- The UI can now call `manager.scan()` in a background thread without blocking the GUI

## Performance Results

**Before**: ~48 seconds total runtime (9-10 seconds per test)
**After**: ~0.11 seconds total runtime (< 0.05 seconds per test)

That's a **~436x performance improvement**!

## Files Modified

1. `app/__init__.py` - Removed non-existent imports from `__getattr__`
2. `app/benchmark_manager.py` - Added `scan()` method, moved discovery out of `__init__`
3. `tests/test_benchmark_manager.py` - Updated tests to properly mock subprocess calls and system capabilities

## Backward Compatibility

All changes are backward compatible:
- The `detect_benchmarks()` method still works as before
- New `scan()` method is optional for UI/background use
- No changes to existing API contracts
- READY_MANUAL logic unchanged as requested