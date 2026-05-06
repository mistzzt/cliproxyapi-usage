{
  description = "cliproxy-usage";

  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
  };

  outputs = {
    self,
    nixpkgs,
  }: let
    systems = [
      "x86_64-linux"
      "aarch64-linux"
      "x86_64-darwin"
      "aarch64-darwin"
    ];

    forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f system);
  in {
    packages = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
      python = pkgs.python314;

      frontend-node-modules =
        pkgs.runCommand "cliproxy-usage-frontend-node-modules" {
          src = ./frontend;
          nativeBuildInputs = [pkgs.bun];
          outputHashAlgo = "sha256";
          outputHashMode = "recursive";
          outputHash =
            {
              x86_64-linux = "sha256-vKLoVrM6VZn2gbzi1gVLu2ZYPPfk/eP+LGOduTiZIVE=";
              aarch64-linux = pkgs.lib.fakeHash;
              x86_64-darwin = pkgs.lib.fakeHash;
              aarch64-darwin = "sha256-RSSGjrkh73JqJrfXgG/9m86HuQfL5ULs6JV8lzmJCEM=";
            }.${
              system
            } or (throw "unsupported system: ${system}");
        } ''
          cp -r $src/* .
          export HOME=$TMPDIR
          export BUN_INSTALL_CACHE_DIR=$(mktemp -d)
          bun install --no-progress --frozen-lockfile --no-cache
          mkdir -p $out
          cp -R ./node_modules $out/
        '';

      frontend = pkgs.stdenv.mkDerivation {
        name = "cliproxy-usage-frontend";
        src = ./frontend;

        nativeBuildInputs = with pkgs; [
          bun
          nodejs
        ];

        buildPhase = ''
          export HOME=$TMPDIR
          cp -R ${frontend-node-modules}/node_modules .
          chmod -R u+w node_modules
          patchShebangs node_modules
          bun run build
        '';

        installPhase = ''
          cp -r dist $out
        '';
      };

      cliproxy-usage = python.pkgs.buildPythonApplication {
        pname = "cliproxy-usage";
        version = "0.1.0";
        pyproject = true;

        src = pkgs.lib.fileset.toSource {
          root = ./.;
          fileset = pkgs.lib.fileset.unions [
            ./pyproject.toml
            ./src
          ];
        };

        build-system = [python.pkgs.uv-build];

        dependencies = with python.pkgs; [
          fastapi
          httpx
          pydantic
          pydantic-settings
          uvicorn
        ];

        pythonRelaxDeps = true;

        postPatch = ''
          substituteInPlace src/cliproxy_usage_server/main.py \
            --replace-fail \
              'Path(__file__).resolve().parents[2] / "frontend" / "dist"' \
              'Path("${frontend}")'
        '';

        pythonImportsCheck = [
          "cliproxy_usage_collect"
          "cliproxy_usage_server"
        ];
      };
    in {
      inherit frontend cliproxy-usage;
      default = cliproxy-usage;
    });

    devShells = forAllSystems (system: let
      pkgs = nixpkgs.legacyPackages.${system};
    in {
      default = with pkgs;
        mkShell {
          buildInputs = [
            python314
            uv
            bun
          ];
        };
    });
  };
}
