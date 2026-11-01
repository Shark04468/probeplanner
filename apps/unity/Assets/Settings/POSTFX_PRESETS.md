# BrainPlanner Runtime PostFX Presets

This project now includes a runtime post-processing bootstrap:

- Script: `Assets/Scripts/BrainView/RuntimePostFxController.cs`
- Auto-wired by: `Assets/Scripts/BrainController.cs`
- Bridge API: `Assets/Scripts/Bridge/ReactBridge.cs`

## Presets

Use bridge call `SetPostFxPreset(<name>)` where `<name>` is:

- `balanced`
- `highcontrast` (default)
- `publication`

Each preset configures:

- Tonemapping (ACES)
- ColorAdjustments (exposure/contrast/saturation)
- Bloom (threshold/intensity/scatter)
- Vignette (intensity/smoothness)
- SSAO renderer feature (if available in current URP renderer)

## Anti-aliasing

Use bridge call `SetAntiAliasingMode(<mode>)`:

- `none`
- `fxaa`
- `smaa`
- `taa` (default, with fallback if unavailable)

## Runtime switches

- `SetPostFxEnabled(true|false)`
- `SetAmbientOcclusionEnabled(true|false)`

## Notes

- `URP-HighFidelity.asset` now forces depth/opaque texture (`m_RequireDepthTexture=1`, `m_RequireOpaqueTexture=1`) for more stable post effects.
- The runtime bootstrap clones profile settings at runtime, so scene assets are not permanently overwritten by play-mode adjustments.
