#!/usr/bin/env -S emacs --script
(dolist (dir (split-string (or (getenv "W_ORG_LOAD_PATH") "") path-separator t))
  (add-to-list 'load-path (expand-file-name dir)))
(require 'ox-mediawiki)
;; (princ load-path)
;; (find-file (nth 3 command-line-args))
;; (org-mw-export-to-mediawiki)
(let ((org-document-content "")
      this-read)
  (while (setq this-read (ignore-errors
                           (read-from-minibuffer "")))
    (setq org-document-content (concat org-document-content this-read "\n")))
  (with-temp-buffer
    (org-mode)
    (insert org-document-content)
    (org-mw-export-as-mediawiki)
    (princ (buffer-string))))
      
       

