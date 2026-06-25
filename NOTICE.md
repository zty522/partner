# Third-Party Code and Design References

This project includes or was inspired by code and design from the following open-source projects. Full license texts are provided below.

## Referenced Projects

1. **Hermes Agent** (MIT License)
   - Repository: https://github.com/nousresearch/hermes-agent
   - Used for: CLI architecture inspiration (subcommand structure, TUI patterns)
   - Files affected: partner/cli/*

2. **Hermes Desktop** (MIT License)
   - Repository: https://github.com/fathah/hermes-desktop
   - Used for: Desktop GUI layout and interaction patterns
   - Files affected: partner/desktop_gui/modern/*

3. **OpenClaw** (MIT License)
   - Repository: https://github.com/openclaw/openclaw
   - Used for: Guided onboarding workflow (`partner onboard` command), system tray patterns
   - Files affected: partner/cli/onboard.py

4. **OpenClaw Windows Hub** (MIT License)
   - Repository: https://github.com/openclaw/openclaw-windows-node
   - Used for: Windows-native support patterns, installation flow
   - Files affected: partner/cli/*

5. **CytoBridge Agent** (MIT License)
   - Repository: https://github.com/JackkWangzh/CytoBridge-agent
   - Used for: Single-cell trajectory inference and cell dynamics analysis
   - Integration: partner/agents/manifests/cytobridge.json (agent manifest)
   - The actual CytoBridge source code lives in its own repository; Partner only ships a manifest that describes how to call it. Users install cytobridge-agent separately.

## License Texts

### MIT License

```
MIT License

Copyright (c) <year> <copyright holders>

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, in accordance with the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
```
