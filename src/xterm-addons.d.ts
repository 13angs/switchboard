/**
 * Ambient declarations for the xterm.js addons.
 *
 * `@xterm/addon-fit@0.10.0` and `@xterm/addon-web-links@0.11.0` ship typings
 * that wrap their declarations in `declare module '<pkg>' { ... }` inside a
 * file that already has a top-level `import`. That makes the block a *module
 * augmentation* rather than an ambient declaration, so under
 * `moduleResolution: "bundler"` TypeScript resolves the package to a file with
 * no top-level exports and reports:
 *
 *   TS2305: Module '"@xterm/addon-fit"' has no exported member 'FitAddon'.
 *
 * These shims declare the surface the app actually uses. Drop this file once
 * the upstream addons ship typings that export at the top level.
 */

declare module '@xterm/addon-fit' {
  import type { ITerminalAddon, Terminal } from '@xterm/xterm';

  export interface ITerminalDimensions {
    cols: number;
    rows: number;
  }

  export class FitAddon implements ITerminalAddon {
    constructor();
    activate(terminal: Terminal): void;
    dispose(): void;
    fit(): void;
    proposeDimensions(): ITerminalDimensions | undefined;
  }
}

declare module '@xterm/addon-web-links' {
  import type { ITerminalAddon, Terminal } from '@xterm/xterm';

  export class WebLinksAddon implements ITerminalAddon {
    constructor(handler?: (event: MouseEvent, uri: string) => void);
    activate(terminal: Terminal): void;
    dispose(): void;
  }
}
