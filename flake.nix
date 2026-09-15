{
  description = "Print to a MangoSlab nemonic sticky-note printer, with no vendor driver";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      forAllSystems = f:
        nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAllSystems (pkgs: rec {
        slabprint = pkgs.python3Packages.buildPythonApplication {
          pname = "slabprint";
          version = "0.1.0";
          pyproject = true;
          src = self;

          build-system = [ pkgs.python3Packages.hatchling ];

          dependencies = with pkgs.python3Packages; [ pillow pyusb bleak ];

          # pyusb loads libusb through ctypes at runtime, so it has to be told
          # where the library actually is inside the store.
          makeWrapperArgs = [
            "--set-default SLABPRINT_LIBUSB ${pkgs.libusb1}/lib/libusb-1.0${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}"
          ];

          nativeCheckInputs = [ pkgs.python3Packages.pytestCheckHook ];
          # The suite is pure logic — protocol encoding, bitmap packing, layout,
          # the queue and the HTTP service. Anything needing a real printer is
          # deliberately not tested here.
          pytestFlags = [ "tests" ];

          meta = with pkgs.lib; {
            description = "Print to a MangoSlab nemonic sticky-note printer, with no vendor driver";
            homepage = "https://github.com/shedali/slabprint";
            license = licenses.mit;
            mainProgram = "slabprint";
            platforms = platforms.unix;
          };
        };
        default = slabprint;
      });

      apps = forAllSystems (pkgs: rec {
        slabprint = {
          type = "app";
          program = "${self.packages.${pkgs.system}.slabprint}/bin/slabprint";
        };
        default = slabprint;
      });

      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = [
            (pkgs.python3.withPackages (ps: with ps; [ pillow pyusb bleak hatchling ]))
            pkgs.libusb1
          ];
          env.NEMONIC_LIBUSB =
            "${pkgs.libusb1}/lib/libusb-1.0${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
        };
      });
    };
}
