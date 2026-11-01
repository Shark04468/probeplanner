// UnlitTransparentLine.shader
// 用于 LineRenderer 的简单无光照透明 Shader
// 不受场景光照影响，颜色纯粹由材质 _Color 决定

Shader "BrainPlanner/UnlitTransparentLine"
{
    Properties
    {
        _Color ("Color", Color) = (0.1, 0.8, 0.8, 0.25)
    }

    SubShader
    {
        Tags
        {
            "RenderType"     = "Transparent"
            "Queue"          = "Transparent"
            "RenderPipeline" = "UniversalPipeline"
        }

        Pass
        {
            Name "UnlitLine"
            Tags { "LightMode" = "UniversalForward" }

            ZWrite Off
            ZTest  LEqual
            Blend  SrcAlpha OneMinusSrcAlpha
            Cull   Off

            HLSLPROGRAM
            #pragma vertex   vert
            #pragma fragment frag
            #include "Packages/com.unity.render-pipelines.universal/ShaderLibrary/Core.hlsl"

            struct Attributes
            {
                float4 positionOS : POSITION;
                half4  color      : COLOR;
            };

            struct Varyings
            {
                float4 positionHCS : SV_POSITION;
                half4  color       : COLOR;
            };

            CBUFFER_START(UnityPerMaterial)
                half4 _Color;
            CBUFFER_END

            Varyings vert(Attributes IN)
            {
                Varyings OUT;
                OUT.positionHCS = TransformObjectToHClip(IN.positionOS.xyz);
                // 顶点色 × 材质色（LineRenderer 用顶点色传递渐变）
                OUT.color = IN.color * _Color;
                return OUT;
            }

            half4 frag(Varyings IN) : SV_Target
            {
                return IN.color;
            }
            ENDHLSL
        }
    }

    FallBack "Hidden/InternalErrorShader"
}
