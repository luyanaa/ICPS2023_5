{
  description = "Reproducible ICPS2023_5 verification toolchain";

  inputs.nix-eda.url = "github:fossi-foundation/nix-eda/6.11.0";

  outputs = { self, nix-eda }:
    {
      devShells = nix-eda.forAllSystems (system:
        let
          pkgs = nix-eda.legacyPackages.${system};
          python = pkgs.python3.withPackages (ps: [ ps.klayout ps.numpy ps.scipy ]);
        in {
          default = pkgs.mkShell {
            packages = [ python pkgs.klayout pkgs.ngspice pkgs.ruby pkgs.yosys ];
          };
        });
    };
}
