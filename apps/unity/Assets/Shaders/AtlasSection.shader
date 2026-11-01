Shader "BrainPlanner/AtlasSection"
{
    Properties
    {
        _MainTex ("Actual MRI/CCF section", 2D) = "black" {}
        _HemisphereFilter ("Hemisphere", Float) = 0
    }
    SubShader
    {
        Tags { "RenderType"="TransparentCutout" "Queue"="AlphaTest" "RenderPipeline"="UniversalPipeline" }
        Pass
        {
            Tags { "LightMode"="UniversalForward" }
            Cull Off
            ZWrite On
            ZTest LEqual
            HLSLPROGRAM
            #pragma vertex vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"
            TEXTURE2D(_MainTex); SAMPLER(sampler_MainTex);
            CBUFFER_START(UnityPerMaterial)
                float _HemisphereFilter;
            CBUFFER_END
            struct Attributes { float4 positionOS : POSITION; float2 uv : TEXCOORD0; };
            struct Varyings { float4 positionHCS : SV_POSITION; float2 uv : TEXCOORD0; float3 worldPos : TEXCOORD1; };
            Varyings vert(Attributes input)
            {
                Varyings output;
                output.positionHCS = TransformObjectToHClip(input.positionOS.xyz);
                output.worldPos = TransformObjectToWorld(input.positionOS.xyz);
                output.uv = input.uv;
                return output;
            }
            half4 frag(Varyings input) : SV_Target
            {
                if (_HemisphereFilter > .5 && input.worldPos.x < 0) clip(-1);
                if (_HemisphereFilter < -.5 && input.worldPos.x > 0) clip(-1);
                half4 pixel = SAMPLE_TEXTURE2D(_MainTex, sampler_MainTex, input.uv);
                clip(pixel.a - .5);
                return half4(pixel.rgb, 1);
            }
            ENDHLSL
        }
    }
}
