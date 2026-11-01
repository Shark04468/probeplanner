Shader "BrainPlanner/RegionFill"
{
    Properties
    {
        _BaseColor ("Base Color", Color) = (0.2, 0.6, 0.8, 0.45)
        _Alpha ("Alpha", Range(0,1)) = 0.45
        _RimColor ("Rim Color", Color) = (1,1,1,1)
        _RimPower ("Rim Power", Range(0.5,8)) = 3.0
        _RimIntensity ("Rim Intensity", Range(0,2)) = 0.25
        _EmissiveBoost ("Emissive Boost", Range(0,1)) = 0.06
        _Brightness ("Brightness", Range(0.6,1.8)) = 1.18
        _Saturation ("Saturation", Range(0.4,1.8)) = 1.12
        _DeRedStrength ("De-red Strength", Range(0,1)) = 0.65
        _HemisphereFilter ("Hemisphere Filter", Float) = 0
        _UseWeightedOIT ("Use Weighted OIT", Float) = 1
        _OITWeightScale ("OIT Weight Scale", Range(0.2,2.5)) = 1.1
        _DepthWeightNear ("Depth Weight Near", Range(0.3,3.0)) = 1.75
        _DepthWeightFar ("Depth Weight Far", Range(0.05,1.2)) = 0.22
        _DepthWeightExp ("Depth Weight Exponent", Range(0.5,4.0)) = 1.85
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
            "RenderType"="Transparent"
            "Queue"="Transparent"
            "RenderPipeline"="UniversalPipeline"
        }


        Pass
        {
            Name "FrontFace"
            Tags { "LightMode"="UniversalForward" }
            Cull Back
            ZWrite Off
            ZTest LEqual
            Blend SrcAlpha OneMinusSrcAlpha

            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Lighting.hlsl"
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
                half4 _BaseColor;
                half4 _RimColor;
                half _Alpha;
                half _RimPower;
                half _RimIntensity;
                half _EmissiveBoost;
                half _Brightness;
                half _Saturation;
                half _DeRedStrength;
                half _HemisphereFilter;
                half _UseWeightedOIT;
                half _OITWeightScale;
                half _DepthWeightNear;
                half _DepthWeightFar;
                half _DepthWeightExp;
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

            half3 ApplySaturation(half3 color, half sat)
            {
                half luma = dot(color, half3(0.299h, 0.587h, 0.114h));
                return lerp(half3(luma, luma, luma), color, sat);
            }

            half3 ApplyDeRed(half3 color, half strength)
            {
                half gb = max(color.g, color.b);
                half redCap = gb + 0.32h;
                color.r = lerp(color.r, min(color.r, redCap), saturate(strength));
                return color;
            }

            Varyings vert(Attributes IN)
            {
                Varyings OUT;
                OUT.positionHCS = TransformObjectToHClip(IN.positionOS.xyz);
                OUT.worldPos = TransformObjectToWorld(IN.positionOS.xyz);
                OUT.worldNormal = TransformObjectToWorldNormal(IN.normalOS);
                return OUT;
            }

            half ComputeWeightedAlpha(Varyings IN, half alpha)
            {
                if (_UseWeightedOIT < 0.5h) return alpha;
                half depth01 = saturate(IN.positionHCS.z / max(1e-5h, IN.positionHCS.w));
                half depthFactor = pow(saturate(1.0h - depth01), _DepthWeightExp);
                half weight = lerp(_DepthWeightFar, _DepthWeightNear, depthFactor) * _OITWeightScale;
                return saturate(alpha * weight);
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
                half3 L1 = normalize(half3(0.38h, 0.74h, 0.55h));
                half3 L2 = normalize(half3(-0.58h, 0.44h, -0.24h));
                half NdotL1 = saturate(dot(N, L1));
                half NdotL2 = saturate(dot(N, L2));
                half3 diffuse = _BaseColor.rgb * (0.70h + 0.24h * NdotL1 + 0.16h * NdotL2);
                half3 H = normalize(L1 + V);
                half spec = pow(saturate(dot(N, H)), 18.0h) * 0.34h;
                half rim = pow(saturate(1.0h - dot(N, V)), _RimPower);
                half3 rimColor = _RimColor.rgb * rim * _RimIntensity;
                if (ContactSliceBypass(IN.worldPos.x, _ContactHighlight, _ContactHemispheres))
                {
                    rimColor += _RimColor.rgb * 0.18h;
                }
                half3 color = diffuse + spec + rimColor + _BaseColor.rgb * (_EmissiveBoost + 0.10h);
                color = ApplySaturation(color, _Saturation);
                color = ApplyDeRed(color, _DeRedStrength);
                color = max(color, _BaseColor.rgb * 0.38h);
                half focusLift = lerp(0.90h, 1.16h, saturate(_FocusedScale * 0.66h));
                color = saturate(color * (_Brightness * focusLift) + 0.03h);
                half alpha = ComputeWeightedAlpha(IN, _Alpha * 0.84h);
                alpha = saturate(alpha * lerp(0.86h, 1.18h, saturate(_FocusedScale)));
                if (ContactSliceBypass(IN.worldPos.x, _ContactHighlight, _ContactHemispheres)) alpha = max(alpha, _Alpha * 0.78h);
                return half4(color, alpha);
            }
            ENDHLSL
        }
    }
}
