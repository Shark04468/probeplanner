using System;

namespace BrainPlanner.Bridge
{
    public static class BackendAddress
    {
        public static string Resolve(string pageUrl, string editorFallback)
        {
            if (string.IsNullOrEmpty(pageUrl)) return Validate(editorFallback);
            if (!Uri.TryCreate(pageUrl, UriKind.Absolute, out var page))
                throw new ArgumentException("Invalid viewer URL.");
            foreach (var pair in page.Query.TrimStart('?').Split('&'))
            {
                int equals = pair.IndexOf('=');
                if (equals < 0) continue;
                string name = Uri.UnescapeDataString(pair.Substring(0, equals));
                if (name != "backend") continue;
                return Validate(Uri.UnescapeDataString(pair.Substring(equals + 1)));
            }
            return Validate(page.GetLeftPart(UriPartial.Authority));
        }

        private static string Validate(string address)
        {
            if (!Uri.TryCreate(address, UriKind.Absolute, out var uri)
                || (uri.Scheme != Uri.UriSchemeHttp && uri.Scheme != Uri.UriSchemeHttps)
                || !string.IsNullOrEmpty(uri.UserInfo)
                || !string.IsNullOrEmpty(uri.Query)
                || !string.IsNullOrEmpty(uri.Fragment))
                throw new ArgumentException("Backend must be an HTTP(S) base URL without credentials, query or fragment.");
            return uri.AbsoluteUri.TrimEnd('/');
        }
    }
}
