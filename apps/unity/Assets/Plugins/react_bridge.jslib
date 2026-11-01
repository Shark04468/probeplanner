// react_bridge.jslib
// Unity WebGL JS 插件
// 编译时合并进 Unity WebGL Build 的 JavaScript 层

mergeInto(LibraryManager.library, {

    /**
     * Unity → React：发送事件
     * C# 调用：BP_NotifyReact(eventName, jsonData)
     */
    BP_NotifyReact: function(eventNamePtr, jsonDataPtr) {
        var eventName = UTF8ToString(eventNamePtr);
        var jsonData  = UTF8ToString(jsonDataPtr);

        var payload = {
            source: 'BrainPlannerUnity',
            event:  eventName,
            data:   jsonData,
        };

        // 方式1：postMessage 给父页面（iframe 嵌入模式）
        try {
            if (window.parent && window.parent !== window) {
                window.parent.postMessage(payload, '*');
            }
        } catch(e) {}

        // 方式2：同页面回调（非 iframe 模式）
        try {
            if (typeof window.onBrainPlannerEvent === 'function') {
                window.onBrainPlannerEvent(eventName, jsonData);
            }
        } catch(e) {}

        // 方式3：CustomEvent（React useEffect addEventListener）
        try {
            var ce = new CustomEvent('BrainPlannerEvent', {
                detail: { event: eventName, data: jsonData }
            });
            window.dispatchEvent(ce);
        } catch(e) {}
    },

    /**
     * Unity → 浏览器控制台日志
     */
    BP_LogToConsole: function(messagePtr) {
        var msg = UTF8ToString(messagePtr);
        console.log('[BrainPlanner-Unity]', msg);
    },

    /**
     * 获取浏览器视口尺寸（供 Unity 动态调整分辨率）
     */
    BP_GetViewportWidth: function() {
        return window.innerWidth || document.documentElement.clientWidth;
    },

    BP_GetViewportHeight: function() {
        return window.innerHeight || document.documentElement.clientHeight;
    },

    /**
     * 检查 Flask 是否可用（异步，结果通过事件返回）
     */
    BP_CheckFlask: function(urlPtr) {
        var url = UTF8ToString(urlPtr);
        fetch(url + '/api/status', { mode: 'cors' })
            .then(function(r) {
                var ok = r.ok ? 'true' : 'false';
                var ce = new CustomEvent('BrainPlannerEvent', {
                    detail: { event: 'flaskStatus', data: '{"ok":' + ok + '}' }
                });
                window.dispatchEvent(ce);
            })
            .catch(function() {
                var ce = new CustomEvent('BrainPlannerEvent', {
                    detail: { event: 'flaskStatus', data: '{"ok":false}' }
                });
                window.dispatchEvent(ce);
            });
    },

});
