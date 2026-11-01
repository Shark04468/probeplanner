Shader "BrainPlanner/RegionOutline"
{
    Properties
    {
        _RegionColor ("Region Color", Color) = (0.1, 0.8, 0.8, 1.0)
        _OutlineAlpha ("Outline Alpha", Range(0,1)) = 0.22
        _RimIntensity ("Rim Intensity", Range(0,2)) = 0.0
        _WireWidth ("Wire Width", Range(0.001,0.05)) = 0.008
        _HemisphereFilter ("Hemisphere Filter (-1 left, 0 all, 1 right)", Float) = 0
        _SliceEnabled ("Slice Enabled", Float) = 0
        _SliceY ("Slice Y (Legacy)", Float) = 0
        _SliceAxis ("Slice Axis (0=X,1=Y,2=Z)", Float) = 1
        _SliceCenter ("Slice Center", Float) = 0
        _SliceThickness ("Slice Thickness (mm)", Float) = 0.25
        _SliceUseSlab ("Slice Use Slab", Float) = 0
        _ContactOverride ("Contact Slice Override", Float) = 0
        _ContactHemispheres ("Sampled Contact Sides (L,R,Midline)", Vector) = (0,0,0,0)
        _ContactStyle ("Contact Style", Float) = 2
        _ContactHighlight ("Contact Highlight", Float) = 0
        _FocusedScale ("Focused Scale", Float) = 1
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Transparent"
            "Queue" = "Transparent-10"
            "RenderPipeline" = "UniversalPipeline"
        }

        Pass
        {
            Name "Outline"
            Tags { "LightMode" = "UniversalForward" }

            Cull Back
            ZWrite Off
            ZTest LEqual
            Blend SrcAlpha OneMinusSrcAlpha

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "ContactClipping.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                float3 normalOS : NORMAL;
            };

            struct Varyings
            {
                float4 positionHCS : SV_POSITION;
                float3 worldPos : TEXCOORD0;
                float3 worldNormal : TEXCOORD1;
            };

            CBUFFER_START(UnityPerMaterial)
                half4 _RegionColor;
                half _OutlineAlpha;
                half _WireWidth;
                half _HemisphereFilter;
                half _SliceEnabled;
                half _SliceY;
                half _SliceAxis;
                half _SliceCenter;
                half _SliceThickness;
                half _SliceUseSlab;
                half _ContactOverride;
                half4 _ContactHemispheres;
                half _ContactStyle;
                half _ContactHighlight;
                half _FocusedScale;
            CBUFFER_END

            Varyings vert(Attributes IN)
            {
                Varyings OUT;
                OUT.positionHCS = TransformObjectToHClip(IN.positionOS.xyz);
                OUT.worldPos = TransformObjectToWorld(IN.positionOS.xyz);
                OUT.worldNormal = TransformObjectToWorldNormal(IN.normalOS);
                return OUT;
            }

            half4 frag(Varyings IN) : SV_Target
            {
                if (_HemisphereFilter > 0.5h && IN.worldPos.x < 0) clip(-1);
                if (_HemisphereFilter < -0.5h && IN.worldPos.x > 0) clip(-1);
                half sliceCoord = (_SliceAxis < 0.5h) ? IN.worldPos.x : ((_SliceAxis < 1.5h) ? IN.worldPos.y : IN.worldPos.z);
                if (_SliceEnabled > 0.5h && !ContactSliceBypass(IN.worldPos.x, _ContactOverride, _ContactHemispheres))
                {
                    if (_SliceUseSlab > 0.5h)
                    {
                        half halfT = max(0.0005h, _SliceThickness * 0.5h);
                        if (abs(sliceCoord - _SliceCenter) > halfT) clip(-1);
                    }
                    else
                    {
                        if (sliceCoord > _SliceCenter) clip(-1);
                    }
                }
                half3 N = normalize(IN.worldNormal);
                half3 V = normalize(GetWorldSpaceViewDir(IN.worldPos));
                half facing = abs(dot(N, V));
                half edgeWidth = max(0.025h, fwidth(facing) * 1.5h);
                half edge = 1.0h - smoothstep(0.0h, edgeWidth, facing);
                // Outline the actual surface and clipping boundaries, not a filled backface shell.
                if (_SliceEnabled > 0.5h && !ContactSliceBypass(IN.worldPos.x, _ContactOverride, _ContactHemispheres))
                {
                    half distanceToCut = abs(sliceCoord - _SliceCenter);
                    if (_SliceUseSlab > 0.5h)
                        distanceToCut = abs(distanceToCut - max(0.0005h, _SliceThickness * 0.5h));
                    half cutWidth = max(_WireWidth, fwidth(sliceCoord) * 1.5h);
                    edge = max(edge, 1.0h - smoothstep(0.0h, cutWidth, distanceToCut));
                }
                if (abs(_HemisphereFilter) > 0.5h)
                {
                    half cutWidth = max(_WireWidth, fwidth(IN.worldPos.x) * 1.5h);
                    edge = max(edge, 1.0h - smoothstep(0.0h, cutWidth, abs(IN.worldPos.x)));
                }
                clip(edge - 0.005h);
                half3 L = normalize(half3(0.35h, 0.72h, 0.45h));
                half ndl = saturate(dot(abs(N), L));
                half3 shaded = _RegionColor.rgb * (0.72h + 0.38h * ndl) + _RegionColor.rgb * 0.06h;
                shaded = saturate(shaded * lerp(0.92h, 1.20h, saturate(_FocusedScale * 0.7h)));
                if (ContactSliceBypass(IN.worldPos.x, _ContactHighlight, _ContactHemispheres))
                {
                    shaded = saturate(lerp(shaded, _RegionColor.rgb * 1.22h, 0.36h));
                }
                half alpha = saturate(_OutlineAlpha);
                alpha = saturate(alpha * lerp(0.82h, 1.16h, saturate(_FocusedScale)));
                if (ContactSliceBypass(IN.worldPos.x, _ContactHighlight, _ContactHemispheres)) alpha = max(alpha, _OutlineAlpha * 1.25h);
                return half4(saturate(shaded), alpha * edge);
            }
            ENDHLSL
        }

        Pass
        {
            Name "Clear"
            Tags { "LightMode" = "UniversalForward" }

            Cull Back
            ZWrite Off
            ColorMask 0

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "ContactClipping.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
            };

            struct Varyings
            {
                float4 positionHCS : SV_POSITION;
                float3 worldPos : TEXCOORD0;
            };

            CBUFFER_START(UnityPerMaterial)
                half _HemisphereFilter;
                half _SliceEnabled;
                half _SliceY;
                half _SliceAxis;
                half _SliceCenter;
                half _SliceThickness;
                half _SliceUseSlab;
                half _ContactOverride;
                half4 _ContactHemispheres;
                half _ContactStyle;
                half _ContactHighlight;
                half _FocusedScale;
            CBUFFER_END

            Varyings vert(Attributes IN)
            {
                Varyings OUT;
                OUT.positionHCS = TransformObjectToHClip(IN.positionOS.xyz);
                OUT.worldPos = TransformObjectToWorld(IN.positionOS.xyz);
                return OUT;
            }

            half4 frag(Varyings IN) : SV_Target
            {
                if (_HemisphereFilter > 0.5h && IN.worldPos.x < 0) clip(-1);
                if (_HemisphereFilter < -0.5h && IN.worldPos.x > 0) clip(-1);
                half sliceCoord = (_SliceAxis < 0.5h) ? IN.worldPos.x : ((_SliceAxis < 1.5h) ? IN.worldPos.y : IN.worldPos.z);
                if (_SliceEnabled > 0.5h && !ContactSliceBypass(IN.worldPos.x, _ContactOverride, _ContactHemispheres))
                {
                    if (_SliceUseSlab > 0.5h)
                    {
                        half halfT = max(0.0005h, _SliceThickness * 0.5h);
                        if (abs(sliceCoord - _SliceCenter) > halfT) clip(-1);
                    }
                    else
                    {
                        if (sliceCoord > _SliceCenter) clip(-1);
                    }
                }
                return 0;
            }
            ENDHLSL
        }
    }

    FallBack "Universal Render Pipeline/Unlit"
}
