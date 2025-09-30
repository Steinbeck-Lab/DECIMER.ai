import sys
import json
from typing import List
from rdkit import Chem


def decode_mol_block_array(str_mol_block_arr: str) -> List[str]:
    """
    This function takes json_encoded array from the StoutController in Laravel
    and returns a list of mol block strings that can be processed with RDKit

    Args:
        str_mol_block_arr (str): JSON string with mol block strings

    Returns:
        List[str]: List of mol block strings
    """
    try:
        mol_block_arr = json.loads(str_mol_block_arr)
        if not isinstance(mol_block_arr, list):
            return []
        return mol_block_arr
    except json.JSONDecodeError as e:
        print(f"Error decoding mol block array: {e}", file=sys.stderr)
        return []


def main():
    """
    This script takes a stringified array with mol str from sys.argv and
    prints the corresponding stringified array with SMILES representations
    """
    try:
        if len(sys.argv) < 2:
            print(json.dumps([]))
            return

        mol_block_arr = decode_mol_block_array(sys.argv[1])

        if not mol_block_arr:
            print(json.dumps([]))
            return

    except Exception as e:
        print(f"Error parsing input: {e}", file=sys.stderr)
        print(json.dumps([]))
        return

    smiles = []
    for mol_block_str in mol_block_arr:
        try:
            if (
                mol_block_str and len(mol_block_str) > 102
            ):  # empty mol block str from Ketcher have length of 102
                mol = Chem.MolFromMolBlock(mol_block_str)
                if mol:
                    smiles.append(Chem.MolToSmiles(mol, kekuleSmiles=True))
                else:
                    smiles.append("invalid")
            else:
                smiles.append("invalid")
        except Exception as e:
            print(f"Error processing mol block: {e}", file=sys.stderr)
            smiles.append("invalid")

    print(json.dumps(smiles))


if __name__ == "__main__":
    main()
