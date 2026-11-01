Shader "BrainPlanner/AnatomicalSurface"
{
    Properties
    {
        _BaseColor ("Color", Color) = (0.6,0.75,0.8,0.12)
        _HemisphereFilter ("Hemisphere", Float) = 0
        _SliceEnabled ("Slice Enabled", Float) = 0
        _SliceAxis ("Slice Axis", Float) = 1
        _SliceCenter ("Slice Center", Float) = 0
        _SliceThickness ("Slice Thickness", Float) = 0.25
        _SliceUseSlab ("Slice Slab", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType"="Transparent" "Queue"="Transparent" "RenderPipeline"="UniversalPipeline" }
        Pass
        {
            Tags { "LightMode"="UniversalForward" }
            Cull Back
            ZWrite Off
            ZTest LEqual
            Blend SrcAlpha OneMinusSrcAlpha
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            struct Attributes { float4 positionOS : POSITION; float3 normalOS : NORMAL; };
            struct Varyings
            {
                float4 positionHCS : SV_POSITION;
                float3 worldPos : TEXCOORD0;
                float3 normalWS : TEXCOORD1;
            };
            CBUFFER_START(UnityPerMaterial)
                half4 _BaseColor;
                float _HemisphereFilter, _SliceEnabled, _SliceAxis, _SliceCenter;
                float _SliceThickness, _SliceUseSlab;
            CBUFFER_END
            Varyings vert(Attributes input)
            {
                Varyings output;
                output.positionHCS = TransformObjectToHClip(input.positionOS.xyz);
                output.worldPos = TransformObjectToWorld(input.positionOS.xyz);
                output.normalWS = TransformObjectToWorldNormal(input.normalOS);
                return output;
            }
            half4 frag(Varyings input) : SV_Target
            {
                if (_HemisphereFilter > 0.5 && input.worldPos.x < 0) clip(-1);
                if (_HemisphereFilter < -0.5 && input.worldPos.x > 0) clip(-1);
                float coordinate = _SliceAxis < 0.5 ? input.worldPos.x :
                    (_SliceAxis < 1.5 ? input.worldPos.y : input.worldPos.z);
                if (_SliceEnabled > 0.5)
                {
                    if (_SliceUseSlab > 0.5)
                        clip(max(0.0005, _SliceThickness * 0.5) - abs(coordinate - _SliceCenter));
                    else
                        clip(_SliceCenter - coordinate);
                }
                half light = 0.65h + 0.35h * saturate(dot(normalize(input.normalWS), normalize(float3(0.4,0.8,-0.5))));
                return half4(_BaseColor.rgb * light, _BaseColor.a);
            }
            ENDHLSL
        }
    }
}
