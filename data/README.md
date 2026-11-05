# Données de l'Assemblée nationale

Source : open data officiel, [data.assemblee-nationale.fr](https://data.assemblee-nationale.fr), 17e législature.

```bash
python data/fetch_scrutins.py            # télécharge si besoin, puis génère candidats.csv
python data/fetch_scrutins.py --refresh  # force le retéléchargement
python data/fetch_scrutins.py --all      # exporte tous les scrutins (scrutins_tous.csv, non versionné)
```

Les fichiers bruts (~35 Mo) sont téléchargés dans `data/raw/`, qui n'est pas versionné.

## `candidats.csv`

Il contient les scrutins susceptibles de départager les groupes. Une ligne est retenue si elle remplit les deux conditions suivantes :

- **clivage ≥ 0,15** : le camp minoritaire (en sièges des groupes « pour » face aux groupes « contre ») pèse au moins 15 % ;
- **son type** est l'un de ceux-ci :
  - un scrutin solennel ;
  - une motion de censure ;
  - un vote sur l'ensemble d'un texte avec au moins 150 votants ;
  - un autre vote (amendement, article, motion de procédure) avec au moins 350 votants.

| Colonne | Contenu |
|---|---|
| `categorie` | solennel, ensemble du texte, motion de censure, motion de procédure (rejet ou renvoi, souvent tactique), amendement / article |
| `clivage` | 0 = tous les groupes du même côté, 0,5 = hémicycle coupé en deux |
| `LFI-NFP` … `NI` | Position majoritaire du groupe (`P` pour, `C` contre, `A` abstention) et part des votants du groupe qui l'ont suivie. Un `*` signale un groupe divisé (moins de 75 %). Une case vide signifie que personne n'a voté dans ce groupe. |
| `lien` | Fiche du scrutin sur le site de l'Assemblée |

Pour une **motion de censure**, seuls les votes « pour » sont recueillis. Un groupe qui ne la vote pas est donc compté « contre ».

Les groupes sont ceux du moment du vote. Le code `PO847173` (groupe UDR d'octobre 2024 à juillet 2025) n'est plus dans le fichier des organes actifs : il est remis à la main dans `GROUPES_HISTORIQUES`.

## Sélection et construction des données du proto

- `selection.json` contient les scrutins retenus à la main, un seul vote par texte. C'est le seul choix éditorial. Pour chacun :
  - l'axe (`eco`, `soc`, `env`, `eur`) ;
  - le sens du « pour » (`dir` = +1 si voter pour pousse vers le pôle « pos » de l'axe).

  Tout ce qui est affiché dans l'appli vient des sources officielles :
  - le **titre du dossier législatif** ;
  - l'**objet du vote** ;
  - un **lien vers le texte complet** (`/dyn/opendata/{id}.html`) : le dernier texte de l'Assemblée déposé avant le vote, c'est-à-dire le texte de la commission, le texte adopté ou le texte déposé ;
  - un lien vers le **dossier législatif**.

  L'open data ne fournit **pas de résumé neutre**. Le seul texte explicatif est l'exposé des motifs, rédigé par les auteurs, donc partisan. On n'affiche donc ni résumé, ni arguments, ni avis d'associations.

  Le dossier est trouvé grâce aux références de vote contenues dans les dossiers. À défaut, il est trouvé par correspondance du titre.

  Limite : pour un vote sur le texte de la commission mixte paritaire (CMP), le lien pointe vers la dernière version de l'Assemblée (souvent le texte de sa commission) et non vers le texte de la CMP.
- `groupes.json` contient la couleur de chaque groupe et sa position **provisoire** sur les axes, censée refléter son programme. Ces positions sont à établir proprement et ne doivent pas être déduites des votes.
- Pour régénérer `proto/data.js` à partir de ces deux fichiers et des votes officiels :

```bash
python data/build_data.py
```

Pour chaque groupe, `data.js` contient la répartition de ses votes (pour, contre, abstention) et pas seulement sa position majoritaire. Un groupe divisé compte donc au prorata dans les scores.

Écarté volontairement : les votes tactiques. Exemple : la proposition RN d'abrogation de la réforme des retraites (scrutin n° 217), contre laquelle la gauche a voté alors qu'elle défend l'abrogation sur le fond.
