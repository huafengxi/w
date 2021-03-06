#!/usr/bin/env -S emacs --script
(set-language-environment "UTF-8")
(set-default-coding-systems 'utf-8)
(prefer-coding-system 'utf-8)
(setq coding-system-for-read 'utf-8)
(setq coding-system-for-write 'utf-8)
(modify-coding-system-alist 'file "" 'utf-8-unix)
(let ((org-document-content "")
      this-read)
  (while (setq this-read (ignore-errors (read-from-minibuffer "")))
    (setq org-document-content (concat org-document-content this-read "\n")))
  (with-temp-buffer
    (org-mode)
    (setq org-html-link-org-files-as-html nil)
    (setq org-export-with-toc nil)
    (setq org-export-headline-levels 3)
    (setq org-export-with-sub-superscripts '{})
    (insert org-document-content)
    (org-md-export-as-markdown)
    (while (search-forward "file://" nil t)
            (replace-match "" nil t))
            (goto-char (point-min))
    (while (search-forward ".md" nil t)
           (replace-match ".org" nil t))
    (princ (buffer-string))))

