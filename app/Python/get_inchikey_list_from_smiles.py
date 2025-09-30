import sys
import json
from typing import List
from rdkit import Chem


def decode_smiles_array(str_smiles_arr: str) -> List[str]:
    """
    This function takes json_encoded array of SMILES str from the
    DECIMERController and returns a list of SMILES strings

    Args:
        str_smiles_arr (str): JSON string with SMILES strings

    Returns:
        List[str]: List of SMILES strings
    """
    try:
        smiles_arr = json.loads(str_smiles_arr)
        if not isinstance(smiles_arr, list):
            return []
        return smiles_arr
    except json.JSONDecodeError as e:
        print(f"Error decoding SMILES array: {e}", file=sys.stderr)
        return []


def main():
    """
    This script takes a stringified array with SMILES str from sys.argv and
    prints a stringified list of InChIKeys (for Pubchem queries)
    """
    try:
        if len(sys.argv) < 2:
            print(json.dumps([]))
            return

        smiles_arr = decode_smiles_array(sys.argv[1])

        if not smiles_arr:
            print(json.dumps([]))
            return

    except Exception as e:
        print(f"Error parsing input: {e}", file=sys.stderr)
        print(json.dumps([]))
        return

    inchikey_arr = []
    for smiles in smiles_arr:
        try:
            if smiles and smiles.strip():
                mol = Chem.MolFromSmiles(smiles)
                if mol:
                    inchikey = Chem.MolToInchiKey(mol)
                    inchikey_arr.append(inchikey)
                else:
                    inchikey_arr.append("invalid")
            else:
                inchikey_arr.append("invalid")
        except Exception as e:
            print(f"Error processing SMILES '{smiles}': {e}", file=sys.stderr)
            inchikey_arr.append("invalid")

    print(json.dumps(inchikey_arr))


if __name__ == "__main__":
    main()
