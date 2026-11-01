Shader "BrainPlanner/RegionIdPick"
{
    Properties
    {
        _IdColor ("ID Color", Color) = (0,0,0,1)
        _HemisphereFilter ("Hemisphere Filter", Float) = 0
        _SliceEnabled ("Slice Enabled", Float) = 0
        _SliceY ("Slice Y (Legacy)", Float) = 0
        _SliceAxis ("Slice Axis (0=X,1=Y,2=Z)", Float) = 1
        _SliceCenter ("Slice Center", Float) = 0
        _SliceThickness ("Slice Thickness (mm)", Float) = 0.25
        _SliceUseSlab ("Slice Use Slab", Float) = 0
        _ContactOverride ("Contact Slice Override", Float) = 0
        _ContactHemispheres ("Sampled Contact Sides (L,R,Midline)", Vector) = (0,0,0,0)
    }

    SubShader
    {
        Tags
        {
            "RenderType" = "Opaque"
            "Queue" = "Geometry+20"
            "RenderPipeline" = "UniversalPipeline"
        }

        Pass
        {
            Name "RegionIdPass"
            Tags { "LightMode" = "UniversalForward" }

            Cull Off
            ZWrite On
            ZTest LEqual
            Blend One Zero

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
                half4 _IdColor;
                half _HemisphereFilter;
                half _SliceEnabled;
                half _SliceY;
                half _SliceAxis;
                half _SliceCenter;
                half _SliceThickness;
                half _SliceUseSlab;
                half _ContactOverride;
                half4 _ContactHemispheres;
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
                return half4(_IdColor.rgb, 1.0h);
            }
            ENDHLSL
        }
    }
}
