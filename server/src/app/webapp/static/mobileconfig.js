// Builds an Apple configuration profile (.mobileconfig) that installs a WireGuard tunnel on iOS or
// macOS, in the format the WireGuard project documents (wireguard-apple/MOBILECONFIG.md). Built in
// the browser so the private key inside the config never leaves the device. Also loadable by node
// for the tests.
(function (root) {
  "use strict";

  function escapeXml(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  }

  // opts: platform ("ios" | "macos"), name, config, remoteAddress, identifier, profileUUID, tunnelUUID
  function build(opts) {
    var subType = opts.platform === "macos" ? "com.wireguard.macos" : "com.wireguard.ios";
    var name = escapeXml(opts.name);
    return (
      '<?xml version="1.0" encoding="UTF-8"?>\n' +
      '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n' +
      '<plist version="1.0">\n' +
      "<dict>\n" +
      "  <key>PayloadDisplayName</key>\n  <string>" + name + "</string>\n" +
      "  <key>PayloadType</key>\n  <string>Configuration</string>\n" +
      "  <key>PayloadVersion</key>\n  <integer>1</integer>\n" +
      "  <key>PayloadIdentifier</key>\n  <string>" + escapeXml(opts.identifier) + "</string>\n" +
      "  <key>PayloadUUID</key>\n  <string>" + escapeXml(opts.profileUUID) + "</string>\n" +
      "  <key>PayloadContent</key>\n  <array>\n    <dict>\n" +
      "      <key>PayloadDisplayName</key>\n      <string>VPN</string>\n" +
      "      <key>PayloadType</key>\n      <string>com.apple.vpn.managed</string>\n" +
      "      <key>PayloadVersion</key>\n      <integer>1</integer>\n" +
      "      <key>PayloadIdentifier</key>\n      <string>" + escapeXml(opts.identifier) + ".tunnel</string>\n" +
      "      <key>PayloadUUID</key>\n      <string>" + escapeXml(opts.tunnelUUID) + "</string>\n" +
      "      <key>UserDefinedName</key>\n      <string>" + name + "</string>\n" +
      "      <key>VPNType</key>\n      <string>VPN</string>\n" +
      "      <key>VPNSubType</key>\n      <string>" + subType + "</string>\n" +
      "      <key>VendorConfig</key>\n      <dict>\n" +
      "        <key>WgQuickConfig</key>\n        <string>" + escapeXml(opts.config) + "</string>\n" +
      "      </dict>\n" +
      "      <key>VPN</key>\n      <dict>\n" +
      "        <key>RemoteAddress</key>\n        <string>" + escapeXml(opts.remoteAddress) + "</string>\n" +
      "        <key>AuthenticationMethod</key>\n        <string>Password</string>\n" +
      "      </dict>\n" +
      "    </dict>\n  </array>\n" +
      "</dict>\n</plist>\n"
    );
  }

  var api = { build: build, escapeXml: escapeXml };
  if (typeof module !== "undefined" && module.exports) {
    module.exports = api;
  } else {
    root.microvpnMobileconfig = api;
  }
})(typeof window !== "undefined" ? window : this);
