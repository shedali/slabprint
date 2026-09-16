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
      packages = forAllSystems (pkgs:
        let
          # --clipboard shells out to whichever of these exists. pbpaste cannot
          # help: it only ever emits text, never image data.
          clipboardTools =
            pkgs.lib.optionals pkgs.stdenv.isDarwin [ pkgs.pngpaste ]
            ++ pkgs.lib.optionals pkgs.stdenv.isLinux [ pkgs.wl-clipboard pkgs.xclip ];
        in
        rec {
        slabprint = pkgs.python3Packages.buildPythonApplication {
          pname = "slabprint";
          version = "0.1.0";
          pyproject = true;
          src = self;

          build-system = [ pkgs.python3Packages.hatchling ];

          dependencies = with pkgs.python3Packages; [ pillow pyusb bleak pypdfium2 ];

          # Everything the program reaches for at runtime is pinned here, so the
          # package does not quietly depend on what happens to be installed.
          #
          #   libusb  pyusb loads it through ctypes, so it must be told the path
          #   font    otherwise text falls back to PIL's bitmap default and
          #           prints badly on any machine without the system fonts
          #   PATH    the clipboard readers --clipboard shells out to
          #
          # set-default, not set, so a user can still override either.
          makeWrapperArgs = [
            "--set-default SLABPRINT_LIBUSB ${pkgs.libusb1}/lib/libusb-1.0${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}"
            "--set-default SLABPRINT_FONT ${pkgs.dejavu_fonts}/share/fonts/truetype/DejaVuSans-Bold.ttf"
            "--suffix PATH : ${pkgs.lib.makeBinPath clipboardTools}"
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
            (pkgs.python3.withPackages (ps: with ps; [ pillow pyusb bleak pypdfium2 hatchling ]))
            pkgs.libusb1
          ];
          env.NEMONIC_LIBUSB =
            "${pkgs.libusb1}/lib/libusb-1.0${pkgs.stdenv.hostPlatform.extensions.sharedLibrary}";
        };
      });
    };
}
